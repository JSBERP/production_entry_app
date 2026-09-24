<template>
  <div class="gpe-root">
    <div v-if="pageLoadError" class="gpe-load-error gpe-card">
      <h3>Failed to load page data</h3>
      <p>There was a network or server error. Please check your connection and try again.</p>
      <button type="button" class="gpe-btn primary" @click="retryPageLoad">Retry</button>
    </div>
    <template v-else>
    <div v-if="deskSessionExpired" class="gpe-session-lock" role="alertdialog" aria-modal="true">
      <div class="gpe-session-lock-card">
        <h3>Login expired</h3>
        <p>
          This screen stayed open after the login ended. Rolls already saved on this PC are kept.
          Click <strong>Log in again</strong> — do not Submit or Close Shift from this screen.
        </p>
        <button type="button" class="gpe-btn primary" @click="reloadGsmForLogin">Log in again</button>
      </div>
    </div>
    <div class="gpe-info-strip">
      {{ pageHeading }} — Create SPRs for selected orders, enter rolls, Save Row saves to server. Submit Entry pushes to SPR and submits.
    </div>

    <div class="gpe-page-tabs">
      <button type="button" :class="{ active: pageTab === 'entry' }" @click="pageTab = 'entry'">Entry</button>
      <button v-if="!freezeGsmSummary" type="button" :class="{ active: pageTab === 'summary' }" @click="pageTab = 'summary'">Summary</button>
      <button v-if="!freezeGsmShiftEntries" type="button" :class="{ active: pageTab === 'shift' }" @click="openShiftTab">Shift Entries</button>
      <button v-if="shiftOpened && !freezeGsmPrevShift" type="button" :class="{ active: pageTab === 'prevshift' }" @click="openPrevShiftTab">Prev Shift</button>
    </div>

    <div v-if="pageTab !== 'shift' && pageTab !== 'prevshift'" class="gpe-filters gpe-card">
      <div class="gpe-filter">
        <label>View</label>
        <select v-model="viewScope" @change="fetchOrders" :disabled="freezeGsmDate">
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </div>
      <div class="gpe-filter" v-if="viewScope === 'daily'">
        <label>Planned Date</label>
        <input type="date" v-model="filterDate" @change="fetchOrders" :disabled="freezeGsmDate" />
      </div>
      <div class="gpe-filter" v-else-if="viewScope === 'weekly'">
        <label>Week</label>
        <input type="week" v-model="filterWeek" @change="fetchOrders" :disabled="freezeGsmDate" />
      </div>
      <div class="gpe-filter" v-else>
        <label>Month</label>
        <input type="month" v-model="filterMonth" @change="fetchOrders" :disabled="freezeGsmDate" />
      </div>
      <div class="gpe-filter">
        <label>Unit</label>
        <template v-if="gsmUnitFilterState.unitLocked && gsmUnitFilterState.pool && gsmUnitFilterState.pool.length === 1">
          <span class="gpe-unit-locked">{{ gsmUnitFilterState.pool[0] }} (assigned)</span>
        </template>
        <template v-else-if="shiftOpened && headerUnit && !isGsmAdminUser">
          <span class="gpe-unit-locked">{{ headerUnit }} (locked)</span>
        </template>
        <select v-else v-model="filterUnit" @change="onUnitChange" :disabled="freezeGsmUnit">
          <option value="">Select unit</option>
          <option v-for="u in unitOptions" :key="u" :value="u">{{ u }}</option>
        </select>
      </div>
      <div class="gpe-filter">
        <label>Search</label>
        <input v-model="searchText" type="text" placeholder="Order / party..." />
      </div>
      <button type="button" class="gpe-btn" @click="fetchOrders">Refresh</button>
    </div>

    <!-- Entry tab -->
    <div v-show="pageTab === 'entry'" class="gpe-layout gpe-layout-entry">
      <aside class="gpe-sidebar gpe-card">
        <h3>{{ isLaminationMode ? "Lamination Orders" : "Orders & Jobs" }}</h3>
        <p class="gpe-hint">
          <template v-if="isLaminationMode">Order-based entry. Confirm selection, add fabric/BOPP inputs, then output rolls.</template>
          <template v-else>PP shaft jobs. Confirm selection, then add roll rows.</template>
        </p>
        <div v-if="shiftOpened && selectionLocked && selectedEntries.length" class="gpe-session-panel">
          <div class="gpe-session-panel-head">Locked session · {{ selectedEntries.length }} job(s)</div>
          <div v-for="e in selectedEntries" :key="e.key" class="gpe-session-entry">
            <strong>{{ e.orderCode }}</strong>
            <span>Job {{ e.jobId || e.job_id }} · {{ e.gsm }} GSM</span>
            <span v-if="e.combination_label">{{ e.combination_label }}</span>
          </div>
          <p class="gpe-session-hint">
            <template v-if="shiftOpened">Tick more jobs below to add them to this shift.</template>
            <template v-else>Change run date above to add more jobs — session stays locked.</template>
          </p>
        </div>
        <div v-if="shiftOpened && filterDate !== runDate" class="gpe-sidebar-date-note">
          Shift runs on {{ formatPlannedDate(runDate) }} · order plan from {{ formatPlannedDate(filterDate) }}.
        </div>
        <div v-if="loadingOrders" class="gpe-muted">Loading…</div>
        <div v-else-if="!jobOrderGroups.length" class="gpe-muted">
          No PP-submitted jobs for {{ formatPlannedDate(ordersBrowseDate()) }} / {{ headerUnit || filterUnit || "unit" }}.
        </div>

        <div v-for="grp in displayJobOrderGroups" :key="grp.key" class="gpe-order-card">
          <div class="gpe-order-head">
            <span class="gpe-order-code">{{ grp.orderCode }}</span>
            <button
              v-if="grp.ppId && !grp.isTrial"
              type="button"
              class="gpe-link-btn"
              @click="viewPP(grp.ppId)"
            >View PP</button>
          </div>
          <div v-if="grp.dayTargetKg > 0" class="gpe-order-target">
            <span class="gpe-day-target">Order Tgt {{ formatKg(grp.dayTargetKg) }} Kg</span>
            <span class="gpe-day-rem">Rem {{ formatKg(grp.dayRemKg) }} Kg</span>
          </div>
          <div v-if="grp.quality || grp.color" class="gpe-order-spec">
            <span v-if="grp.quality" class="gpe-spec-chip gpe-spec-quality">{{ grp.quality }}</span>
            <span v-if="grp.color" class="gpe-spec-chip gpe-spec-color">{{ grp.color }}</span>
          </div>
          <label
            v-for="job in grp.jobs"
            :key="job.job_key"
            class="gpe-job-card"
            :class="jobRowClass(job)"
            :title="job.tooltip"
          >
            <input
              type="checkbox"
              class="gpe-line-check"
              :checked="isJobSelected(job)"
              :disabled="!shiftOpened || !job.selectable || (selectionLocked && isJobSelected(job))"
              @change="toggleJob(job, $event)"
            />
            <div class="gpe-job-body" @click.prevent="onJobLabelClick(job)">
              <div class="gpe-job-head">
                <span class="gpe-job-title">Job {{ job.job_id }}</span>
                <span v-if="job.is_manual" class="gpe-spec-chip gpe-spec-manual">Manual</span>
                <span class="gpe-job-head-dot">·</span>
                <span class="gpe-job-gsm">{{ job.gsm }} GSM</span>
              </div>
              <div v-if="job.quality || job.color" class="gpe-job-spec">
                <span v-if="job.quality" class="gpe-spec-chip gpe-spec-quality">{{ job.quality }}</span>
                <span v-if="job.color" class="gpe-spec-chip gpe-spec-color">{{ job.color }}</span>
              </div>
              <div v-if="!isLaminationMode" class="gpe-job-combination">{{ job.combination_label || "—" }}</div>
              <div v-else class="gpe-job-combination">
                <template v-if="job.width_inch || job.widthLabel">{{ job.width_inch || job.widthLabel }}"</template>
                <template v-else-if="job.combination_label">{{ job.combination_label }}</template>
                <template v-else>—</template>
              </div>
              <div v-if="isLaminationMode && (job.lamination_process || grp.laminationProcess)" class="gpe-job-target">
                <span class="gpe-spec-chip">Process {{ job.lamination_process || grp.laminationProcess }}</span>
                <span v-if="(job.lamination_process || grp.laminationProcess) === '107'" class="gpe-spec-chip">Lamination + BOPP</span>
                <span v-else class="gpe-spec-chip">Lamination input</span>
              </div>
              <div v-if="job.job_target_kg > 0" class="gpe-job-target">
                <span class="gpe-day-target">Job Tgt {{ formatKg(job.job_target_kg) }} Kg</span>
                <span class="gpe-day-rem">Rem {{ formatKg(job.job_remaining_kg) }} Kg</span>
              </div>
              <div v-if="!isLaminationMode" class="gpe-dual-meter" :class="{ 'gpe-dual-meter-full': job.quota_full }">
                <div class="gpe-meter-col">
                  <span class="gpe-meter-label">Shafts</span>
                  <span class="gpe-meter-frac">
                    <em>{{ job.job_shafts_produced }}</em><span>/</span><strong>{{ job.max_shafts }}</strong>
                  </span>
                </div>
                <div class="gpe-meter-col">
                  <span class="gpe-meter-label">Rolls</span>
                  <span class="gpe-meter-frac">
                    <em>{{ job.job_rolls_produced }}</em><span>/</span><strong>{{ job.max_rolls }}</strong>
                  </span>
                </div>
              </div>
              <div v-else class="gpe-job-remaining">
                Order remaining — produce by rolls after inputs.
              </div>
              <div class="gpe-meter-context">{{ shift }} · {{ formatPlannedDate(runDate) }}</div>
              <div v-if="cint(job.today_rolls) > 0" class="gpe-shift-breakdown">
                <span class="gpe-shift-today">Today: {{ job.today_rolls }} {{ plural(job.today_rolls, "roll", "rolls") }}</span>
                <span
                  v-for="part in shiftBreakdownParts(job)"
                  :key="part.shift"
                  :class="['gpe-shift-part', part.shift === shift ? 'gpe-shift-current' : 'gpe-shift-other']"
                > · {{ part.shift }}: <strong>{{ part.count }}</strong></span>
              </div>
              <div v-if="job.rem_shafts > 0 || job.rem_rolls > 0" class="gpe-job-remaining">
                Remaining: {{ jobRemainingText(job) }}
              </div>
              <div v-if="job.chip" class="gpe-job-foot">
                <span :class="['gpe-chip', job.chipClass]">{{ job.chip }}</span>
              </div>
            </div>
          </label>
        </div>

        <div v-if="shiftOpened && (headerUnit || filterUnit)" class="gpe-sidebar-section gpe-mix-roll-sidebar">
          <div class="gpe-sidebar-section-head">
            <strong>Mix Rolls</strong>
            <div class="gpe-mix-roll-head-actions">
              <button
                type="button"
                class="gpe-btn primary gpe-btn-sm"
                :disabled="mixRollBusy || addMixRollBusy"
                @click="openAddMixRollDialog"
              >Add Mix Roll</button>
              <button type="button" class="gpe-link-btn" :disabled="mixRollLoading" @click="loadMixRollCandidates">
                {{ mixRollLoading ? "…" : "Refresh" }}
              </button>
            </div>
          </div>
          <p class="gpe-hint">Color Chart items planned this month · {{ headerUnit || filterUnit }} · width set by planning.</p>
          <div v-if="mixRollLoading" class="gpe-muted">Loading mix rolls…</div>
          <div v-else-if="!mixRollCandidates.length" class="gpe-muted">No mix rolls planned this month for this unit (needs item + shaft width).</div>
          <div v-for="mix in mixRollCandidates" :key="mix.date_key + '::' + (mix.mix_id || mix.mix_row_key)" class="gpe-mix-roll-card">
            <div class="gpe-mix-roll-head">
              <strong>{{ mix.label }}</strong>
              <span v-if="mix._submitted" class="gpe-chip gpe-chip-done">Done</span>
              <span v-else-if="activeMixRoll?.spr_name === mix.spr_name && mix.spr_name" class="gpe-chip gpe-chip-draft">Active</span>
            </div>
            <div class="gpe-mix-roll-meta">
              <span>{{ mix.color_transition }}</span>
              <span>{{ mix.gsm }} GSM · {{ mix.shaft || "—" }}</span>
              <span class="gpe-mix-counts">
                Shafts {{ mixProducedShafts(mix) }}/{{ mixRollShaftCount(mix) }}
                · Rolls {{ mixProducedRolls(mix) }}/{{ mixRollMaxRows(mix) }}
              </span>
              <span class="gpe-muted">Chart: {{ formatMixPlanningKey(mix.planning_date_key) }}</span>
              <span v-if="mix.spr_name" class="gpe-muted">SPR: {{ mix.spr_name }}</span>
              <span
                v-if="mix.spr_shift && mix.spr_shift !== shift"
                class="gpe-muted"
              >Opened on {{ mix.spr_shift }}{{ mix.spr_run_date ? " · " + mix.spr_run_date : "" }}</span>
            </div>
            <button
              type="button"
              class="gpe-btn primary gpe-btn-sm"
              :disabled="mixRollBusy || mix._submitted || !mix.item_code"
              @click="startMixRollProduction(mix)"
            >{{ mix.spr_name ? "Continue" : "Start production" }}</button>
          </div>
          <div v-if="shiftOpened && activeMixRoll" class="gpe-mix-active-banner">
            Active: <strong>{{ activeMixRoll.label }}</strong>
            <span class="gpe-muted">{{ activeMixRoll.color_transition }} · {{ activeMixRoll.gsm }} GSM · {{ activeMixRoll.shaft }}</span>
            <span class="gpe-muted">Use Add Roll Row — order code {{ activeMixRoll.label }}</span>
            <button type="button" class="gpe-link-btn" @click="clearActiveMixRoll">Remove from this shift</button>
          </div>
        </div>

        <div v-if="filteredCompletedJobGroups.length" class="gpe-sidebar-section">
          <button type="button" class="gpe-collapse-btn" @click="showCompletedOrders = !showCompletedOrders">
            {{ showCompletedOrders ? "▼" : "▶" }} Completed jobs ({{ completedJobCount }})
          </button>
          <div v-show="showCompletedOrders">
            <div v-for="grp in filteredCompletedJobGroups" :key="'c-' + grp.key" class="gpe-order-card gpe-completed">
              <div class="gpe-order-head">
                <span class="gpe-order-code">{{ grp.orderCode }}</span>
                <button
                  v-if="grp.ppId && !grp.isTrial"
                  type="button"
                  class="gpe-link-btn"
                  @click="viewPP(grp.ppId)"
                >View PP</button>
              </div>
              <div v-if="grp.dayTargetKg > 0" class="gpe-order-target">
                <span class="gpe-day-target">Order Tgt {{ formatKg(grp.dayTargetKg) }} Kg</span>
                <span class="gpe-day-rem">Rem {{ formatKg(grp.dayRemKg) }} Kg</span>
              </div>
              <div v-if="grp.quality || grp.color" class="gpe-order-spec">
                <span v-if="grp.quality" class="gpe-spec-chip gpe-spec-quality">{{ grp.quality }}</span>
                <span v-if="grp.color" class="gpe-spec-chip gpe-spec-color">{{ grp.color }}</span>
              </div>
              <label
                v-for="job in grp.jobs"
                :key="job.job_key"
                class="gpe-job-card gpe-line-disabled"
                :title="job.tooltip"
              >
                <input type="checkbox" class="gpe-line-check" disabled />
                <div class="gpe-job-body">
                  <div class="gpe-job-head">
                    <span class="gpe-job-title">Job {{ job.job_id }}</span>
                    <span v-if="job.is_manual" class="gpe-spec-chip gpe-spec-manual">Manual</span>
                    <span class="gpe-job-head-dot">·</span>
                    <span class="gpe-job-gsm">{{ job.gsm }} GSM</span>
                  </div>
                  <div v-if="job.quality || job.color" class="gpe-job-spec">
                    <span v-if="job.quality" class="gpe-spec-chip gpe-spec-quality">{{ job.quality }}</span>
                    <span v-if="job.color" class="gpe-spec-chip gpe-spec-color">{{ job.color }}</span>
                  </div>
                  <div v-if="!isLaminationMode" class="gpe-job-combination">{{ job.combination_label || "—" }}</div>
                  <div v-else class="gpe-job-combination">
                    <template v-if="job.width_inch || job.widthLabel">{{ job.width_inch || job.widthLabel }}"</template>
                    <template v-else-if="job.combination_label">{{ job.combination_label }}</template>
                    <template v-else>—</template>
                  </div>
                  <div v-if="job.job_target_kg > 0" class="gpe-job-target">
                    <span class="gpe-day-target">Job Tgt {{ formatKg(job.job_target_kg) }} Kg</span>
                    <span class="gpe-day-rem">Rem {{ formatKg(job.job_remaining_kg) }} Kg</span>
                  </div>
                  <div class="gpe-dual-meter gpe-dual-meter-done">
                    <div class="gpe-meter-col">
                      <span class="gpe-meter-label">Shafts</span>
                      <span class="gpe-meter-frac">
                        <em>{{ job.job_shafts_produced }}</em><span>/</span><strong>{{ job.max_shafts }}</strong>
                      </span>
                    </div>
                    <div class="gpe-meter-col">
                      <span class="gpe-meter-label">Rolls</span>
                      <span class="gpe-meter-frac">
                        <em>{{ job.job_rolls_produced }}</em><span>/</span><strong>{{ job.max_rolls }}</strong>
                      </span>
                    </div>
                  </div>
                  <div class="gpe-job-foot">
                    <span :class="['gpe-chip', job.chipClass]">{{ job.chip }}</span>
                  </div>
                </div>
              </label>
            </div>
          </div>
        </div>
      </aside>

      <main class="gpe-main gpe-card">
        <div class="gpe-header gpe-session-panel-main gpe-card-inner">
          <div class="gpe-session-head-row">
            <h3 class="gpe-session-title">Production Session</h3>
            <div v-if="shiftOpened && shiftBatchPrefix" class="gpe-batch-badge-wrap">
              <span class="gpe-batch-badge">{{ shift }} · {{ shiftBatchPrefix }}</span>
            </div>
            <button
              type="button"
              class="gpe-btn gpe-hide-session-btn"
              :title="sessionHeaderCollapsed ? 'Show session details' : 'Hide session details to see more roll rows'"
              @click="sessionHeaderCollapsed = !sessionHeaderCollapsed"
            >{{ sessionHeaderCollapsed ? "Show session" : "Hide session" }}</button>
            <button
              v-if="shiftOpened"
              type="button"
              class="gpe-btn gpe-btn-warn gpe-close-shift-btn"
              :disabled="shiftClosingBusy || deskSessionExpired"
              @click="closeShift"
            >
              {{ shiftClosingBusy ? "Closing…" : "Close Shift" }}
            </button>
          </div>
          <div v-if="shiftResumeBanner" class="gpe-resume-banner">{{ shiftResumeBanner }}</div>
          <div v-show="!sessionHeaderCollapsed">
          <div v-if="shiftOpened && shiftOpenedBy" class="gpe-shift-opened-by">
            Opened by {{ shiftOpenedBy }}
          </div>
          <div v-if="shiftStatusChips.length" class="gpe-shift-status-strip gpe-shift-status-strip-compact">
            <span v-for="chip in shiftStatusChips" :key="'h-' + chip.shift" :class="['gpe-shift-chip', chip.tone]">
              {{ chip.shift }}: {{ chip.label }}
            </span>
          </div>
          <div v-if="shiftOpenPromptVisible" class="gpe-shift-not-started-banner">
            <span>
              <strong>{{ shift }}</strong> not started for {{ formatPlannedDate(runDate) }} · Unit {{ headerUnit }}.
              Change Run Date or Shift above, then click Start Shift.
            </span>
            <button type="button" class="gpe-btn primary" :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_start_shift')" @click="guardedStartShift">Start Shift</button>
          </div>
          <div class="gpe-tags" v-if="!selectionLocked && headerTags.length">
            <span v-for="t in headerTags" :key="t" class="gpe-tag">{{ t }}</span>
          </div>
          <div class="gpe-header-session-row">
            <div class="gpe-header-fields gpe-header-fields-lg">
              <label>Run Date <input type="date" v-model="runDate" :disabled="shiftOpened" /></label>
              <label>
                Shift
                <select v-model="shift" :disabled="shiftOpened" @change="onShiftHeaderChange">
                  <option value="Day Shift">Day Shift</option>
                  <option value="Night Shift">Night Shift</option>
                </select>
              </label>
              <label>Unit <input v-model="headerUnit" type="text" readonly /></label>
              <div v-if="(headerUnit || filterUnit) && !isMixingExcluded" class="gpe-wastage-recycle-btns">
                <button
                  type="button"
                  class="gpe-btn"
                  :disabled="!canOpenMixingSheet"
                  :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_mixing_sheet')"
                  @click="guardedMixingSheet"
                >Mixing Sheet</button>
                <button
                  type="button"
                  class="gpe-btn"
                  :disabled="!canOpenMixingSheet"
                  @click="openBreakdownDialog"
                >Breakdown</button>
              </div>
              <div v-if="shiftOpened && sessionSprList.length" class="gpe-wastage-recycle-btns">
                <button
                  type="button"
                  class="gpe-btn warn"
                  :disabled="!canOpenWastageRecycle"
                  @click="openWastageDialog"
                >Wastage</button>
                <button
                  v-if="!isLaminationMode"
                  type="button"
                  class="gpe-btn"
                  :disabled="!canOpenWastageRecycle"
                  @click="openRecycleDialog"
                >Recycle</button>
                  <div class="gpe-quality-check-wrap" v-click-outside="closeQualityMenu">
                    <button
                      type="button"
                      class="gpe-btn"
                      :disabled="!canOpenQualityCheck"
                      @click.stop="toggleQualityMenu"
                    >Quality Check ▾</button>
                    <div v-if="qualityMenuOpen && canOpenQualityCheck" class="gpe-quality-menu">
                      <button type="button" @click.stop="runQualityCheck('round_gsm')">Round Cutting GSM Test</button>
                      <button type="button" @click.stop="runQualityCheck('patty_gsm')">Patty Cutting GSM Test</button>
                      <button type="button" @click.stop="runQualityCheck('tensile')">Tensile Testing</button>
                      <button type="button" @click.stop="runQualityCheck('colour_spectrum')">Colour Spectrum</button>
                    </div>
                  </div>
                  <button
                    type="button"
                    class="gpe-btn"
                    :disabled="!canOpenLotSample"
                    @click="openLotSampleDialog"
                  >Lot Sample</button>
                  <button
                    type="button"
                    class="gpe-btn"
                    :disabled="!canOpenShiftConsumables"
                    @click="openShiftConsumablesDialog"
                  >Shift Consumables</button>
              </div>
              <label v-if="shiftOpened">Operator <input :value="operator" type="text" readonly /></label>
              <label v-if="shiftOpened">Supervisor <input :value="supervisor" type="text" readonly /></label>
              <label v-if="shiftOpened">Co-ordinator <input :value="coordinator" type="text" readonly /></label>
            </div>
            <div v-if="sessionSprList.length" class="gpe-spr-table-wrap gpe-spr-inline">
              <div class="gpe-spr-table-title">Order · Label Type · SPR</div>
              <table class="gpe-spr-table">
                <thead>
                  <tr><th>Order Code</th><th>Label Type</th><th>SPR</th></tr>
                </thead>
                <tbody>
                  <tr v-for="s in sessionSprList" :key="s.pp_id">
                    <td>{{ s.order_code }}</td>
                    <td>{{ s.label_type || "Default" }}</td>
                    <td>
                      <button type="button" class="gpe-link-btn" @click="openSpr(s.spr_name)">{{ s.spr_name }}</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-if="selectedSummary || selectedEntries.length" class="gpe-selection-strip gpe-selection-strip-lg">
            <div class="gpe-selection-text">
              <strong>{{ selectedSummary?.count || 0 }}</strong> job(s)
              <template v-if="selectedSummary?.orderCount > 1"> · {{ selectedSummary.orderCount }} orders</template>
              · Session plan <strong>{{ formatKg(selectedSummary?.dayPlanned || 0) }}</strong> Kg ·
              Session rem <strong>{{ formatKg(metrics.dayRemaining) }}</strong> Kg
              <span v-if="selectionLocked" class="gpe-lock-badge inline">Locked</span>
            </div>
            <div class="gpe-selection-actions">
              <button
                v-if="!selectionLocked"
                type="button"
                class="gpe-btn primary"
                :disabled="!shiftOpened || !selectedEntries.length"
                @click="openConfirmSelection"
              >Confirm selection</button>
              <button
                v-else
                type="button"
                class="gpe-btn"
                @click="unlockSelection"
              >Unlock</button>
              <button type="button" class="gpe-link-btn" @click="clearSelection">Clear</button>
            </div>
          </div>
          </div>
        </div>

        <div class="gpe-entry-workspace" :class="{ 'gpe-session-collapsed': sessionHeaderCollapsed }">
        <div v-show="!sessionHeaderCollapsed" class="gpe-metrics gpe-metrics-compact">
          <div class="gpe-metric slate">Board plan (Kg)<br /><strong>{{ formatKg(boardDayTotalKg) }}</strong></div>
          <div class="gpe-metric blue">Total Entry (Kg)<br /><strong>{{ formatKg(metrics.totalGross) }}</strong></div>
          <div class="gpe-metric green">Net Production (Kg)<br /><strong>{{ formatKg(metrics.totalNet) }}</strong></div>
          <div class="gpe-metric orange">Day remaining (Kg)<br /><strong>{{ formatKg(metrics.dayRemaining) }}</strong></div>
          <div class="gpe-metric grey">Rolls<br /><strong>{{ sessionRollCount }}</strong></div>
        </div>

        <div class="gpe-create-spr-row" v-if="shiftOpened && selectionLocked && selectedEntries.length">
          <div class="gpe-create-spr-row-left">
            <button v-if="needsCreateSprs" type="button" class="gpe-btn primary gpe-btn-create-spr" :disabled="!canCreateSprs" @click="createSprs()">
              {{ mixSprReady ? "Create SPR for new order" : "Create SPR" }}
            </button>
            <span v-else class="gpe-spr-ready-badge" title="Draft SPRs exist for selected orders or active mix roll">SPRs ready ✓</span>
          </div>
        </div>

        <div class="gpe-toolbar">
          <div class="gpe-toolbar-left">
            <button
              v-if="allSelectedJobsMaxed && selectedEntries.length && !mixSprReady"
              type="button"
              class="gpe-btn warn"
              @click="openManualJobForAllMaxed"
            >All jobs at max — Manual Job</button>
            <button v-else type="button" class="gpe-btn primary" :title="addRollDisabledHint" :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_add_row')" @click="guardedAddRollRow">
              {{ addRollInProgress ? "Adding…" : "Add Roll Row" }}
            </button>
            <button type="button" class="gpe-btn" :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_remove_row')" @click="guardedRemoveRow">Remove Row</button>
            <button
              type="button"
              class="gpe-btn gpe-btn-warn"
              title="Clear grid including mix roll rows — server SPRs are kept"
              :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_clear_entries')"
              @click="guardedClearEntries"
            >Clear Entries</button>
          </div>
          <span class="gpe-save-status">{{ saveStatus }}</span>
          <div class="gpe-toolbar-right">
            <button
              v-if="showRollInputsBtn"
              type="button"
              class="gpe-btn"
              :disabled="!toolsEnabled"
              :title="toolsHint || 'Select RM batches for WO consumption'"
              :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_tools')"
              @click="runTool('rmbatches')"
            >Roll Inputs</button>
            <div class="gpe-tools-wrap" v-click-outside="closeToolsMenu">
              <button
                type="button"
                class="gpe-btn"
                :disabled="!toolsEnabled"
                :title="toolsHint"
                :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_tools')"
                @click="guardedToolsToggle"
              >Tools ▾</button>
              <div v-if="toolsMenuOpen && toolsEnabled" class="gpe-tools-menu">
                <button type="button" @click="runTool('manual')">SPR — Manual job</button>
                <button type="button" @click="runTool('trail')">SPR — Trail Order</button>
                <button type="button" @click="runTool('bundle')">SPR — Bundle packaging</button>
                <button type="button" @click="runTool('bundlese')">SPR — Bundle SE on Submit</button>
                <button type="button" @click="runTool('rmbatches')">{{ showRollInputsBtn ? "Roll Inputs / Select RM batches" : "SPR — Select RM batches" }}</button>
                <button v-if="isLaminationMode" type="button" @click="runTool('lamFabricIn')">Lamination — Add Fabric Inputs</button>
                <button v-if="isLaminationMode" type="button" @click="runTool('lamBoppIn')">Lamination — Add BOPP Inputs</button>
                <button v-if="isLaminationMode" type="button" @click="runTool('lamOutput')">Lamination — Add Output Rolls</button>
                <button type="button" @click="runTool('fixshaft')">Fix Shaft Numbers</button>
              </div>
            </div>
            <button type="button" class="gpe-btn" :disabled="!selectedEntries.length" :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_shaft_details')" @click="guardedShaftDetails">Shaft Details</button>
            <button type="button" class="gpe-btn primary" :disabled="!canSubmitEntry || deskSessionExpired" :style="boardActionFrozenStyle(gsmBoardAccess, 'gsm_submit')" @click="guardedSubmitEntry">Submit Entry</button>
          </div>
        </div>

        <div class="gpe-gsm-legend">
          <span class="gpe-legend-title">GSM diff (Sticker vs Produced):</span>
          <span class="gpe-legend-chip gpe-gsm-band-0">|diff| &lt; 1</span>
          <span class="gpe-legend-chip gpe-gsm-band-1">1 – 2</span>
          <span class="gpe-legend-chip gpe-gsm-band-2">2 – 3</span>
          <span class="gpe-legend-chip gpe-gsm-band-3">≥ 3</span>
          <span class="gpe-legend-chip gpe-gsm-incomplete">Awaiting / incomplete</span>
        </div>

        <div class="gpe-grid-wrap gpe-grid-wrap-entry">
          <table class="gpe-grid gpe-grid-entry">
            <thead>
              <tr>
                <th class="gpe-sticky-col gpe-sticky-0">#</th>
                <th class="gpe-sticky-col gpe-sticky-1">Order Code</th>
                <th class="gpe-num">Job</th>
                <th>Quality</th>
                <th>Color</th>
                <th class="gpe-num">Sticker GSM</th>
                <th class="gpe-num">Width (inches)</th>
                <th class="gpe-num">Ordered Length (MTR)</th>
                <th class="gpe-num">Produced Length (MTR)</th>
                <th>Prod GSM</th>
                <th>Batch</th>
                <th>Net (Kgs)</th>
                <th>Gross (Kgs)</th>
                <th>Planned Qty (Kgs)</th>
                <th>UOM</th>
                <th>WO</th>
                <th>Core</th>
                <th class="gpe-num">Core Base Wt (Kg)</th>
                <th>Polybag</th>
                <th class="gpe-num">Diameter (inches)</th>
                <th class="gpe-num">CBM (inches)</th>
                <th>Bay</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in fabricRollLines"
                :key="row._id"
                :class="[rowBandClass(row), { 'gpe-row-locked': row.row_locked, 'gpe-row-wasted': row.is_wasted, 'gpe-row-mix': row.is_mix_roll_row }]"
              >
                <td class="gpe-sticky-col gpe-sticky-0">{{ rollDisplayIndex(row, idx, fabricRollLines.length) }}</td>
                <td class="gpe-sticky-col gpe-sticky-1">
                  {{ row.party_code }}
                  <span v-if="row.is_mix_roll_row" class="gpe-chip gpe-chip-mix">Mix</span>
                </td>
                <td class="gpe-num">{{ row.is_mix_roll_row ? "—" : (row.job_id || row.job || "—") }}</td>
                <td>{{ row.quality }}</td>
                <td>{{ row.color }}</td>
                <td class="gpe-num">{{ row.gsm }}</td>
                <td class="gpe-num">{{ widthDisplay(row) }}</td>
                <td class="gpe-num">{{ row.meter_roll }}</td>
                <td class="gpe-num">
                  <div class="gpe-len-cell">
                  <input
                    v-model.number="row.produced_length_mtrs"
                    type="number"
                    step="1"
                    min="0"
                    inputmode="numeric"
                    class="gpe-inp gpe-inp-len"
                    :disabled="row.row_locked || row.is_bundle_row"
                    @input="onRowEdit(row)"
                  />
                  <span class="gpe-unit-suffix">MTR</span>
                  </div>
                </td>
                <td class="gpe-num">{{ row.produced_gsm }}</td>
                <td class="gpe-num">{{ row.batch_no }}</td>
                <td class="gpe-num">{{ formatKg(row.net_weight) }}</td>
                <td class="gpe-num">
                  <input
                    :value="sprGrossWeightDisplay(row.gross_weight)"
                    type="text"
                    inputmode="decimal"
                    autocomplete="off"
                    class="gpe-inp"
                    :disabled="row.row_locked || row.is_bundle_row"
                    @input="onGrossWeightInput(row, $event)"
                  />
                </td>
                <td class="gpe-num">{{ row.is_mix_roll_row ? "—" : formatKg(row.planned_qty) }}</td>
                <td>{{ row.uom || "Kg" }}</td>
                <td>
                  <a
                    v-if="row.work_order"
                    href="#"
                    class="gpe-wo-link"
                    @click.prevent="openWorkOrder(row.work_order)"
                  >{{ row.work_order }}</a>
                </td>
                <td>
                  <select
                    v-model="row.custom_core_width_mm"
                    class="gpe-inp gpe-inp-wide"
                    :disabled="row.row_locked"
                    @change="onRowEdit(row)"
                  >
                    <option v-for="opt in coreWidthOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                  </select>
                </td>
                <td class="gpe-num">{{ coreBaseWeightDisplay(row) }}</td>
                <td class="gpe-num">
                  <input
                    v-model.number="row.custom_polybag_kgs"
                    type="number"
                    step="0.001"
                    min="0"
                    class="gpe-inp gpe-inp-narrow"
                    :disabled="row.row_locked || row.is_bundle_row"
                    @input="onPolybagInput(row)"
                  />
                </td>
                <td class="gpe-num">
                  <input
                    v-model.number="row.custom_diameter_inches"
                    type="number"
                    step="0.01"
                    min="0"
                    class="gpe-inp gpe-inp-narrow"
                    :disabled="row.row_locked || row.is_bundle_row"
                    @input="onDiameterInput(row)"
                  />
                </td>
                <td class="gpe-num">{{ formatCbm(row.custom_cbm_cubic_meters) }}</td>
                <td>
                  <select
                    v-model="row.custom_bay"
                    class="gpe-inp gpe-bay-select"
                    :disabled="row.row_locked || row.is_bundle_row || row.is_wasted"
                    :title="row.custom_bay || (bayOptions.length ? 'Select bay' : 'No bays for this unit')"
                    @focus="onBaySelectFocus"
                  >
                    <option value="">{{ bayOptions.length ? "—" : "No bays" }}</option>
                    <option v-for="b in bayOptions" :key="b.name" :value="b.name">
                      {{ b.bay_name || b.name }}
                    </option>
                  </select>
                </td>
                <td>
                  <div class="gpe-actions">
                  <button
                    v-if="!row.row_locked"
                    type="button"
                    class="gpe-btn sm"
                    @click="saveRow(row)"
                  >Save Row</button>
                  <button
                    v-else
                    type="button"
                    class="gpe-btn sm"
                    @click="editRow(row)"
                  >Edit Row</button>
                  <button
                    type="button"
                    class="gpe-btn sm gpe-btn-label"
                    :disabled="!isRowLabelReady(row)"
                    :title="isRowLabelReady(row) ? 'Print production label' : 'Save Row first'"
                    @click="printLabel(row)"
                  >Production Label</button>
                  <button
                    type="button"
                    class="gpe-btn sm gpe-btn-qc"
                    :disabled="!isRowLabelReady(row)"
                    :title="isRowLabelReady(row) ? 'Print QC approval label' : 'Save Row first'"
                    @click="printQcLabel(row)"
                  >QC Label</button>
                  <button
                    v-if="row.is_mix_roll_row"
                    type="button"
                    class="gpe-btn sm"
                    @click="removeMixRollRow(row)"
                  >Remove</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        </div>
      </main>
    </div>

    <!-- Summary tab -->
    <div v-show="pageTab === 'summary'" class="gpe-main gpe-card gpe-summary-tab">
      <div class="gpe-tabs">
        <button :class="{ active: summaryTab === 'summary' }" @click="summaryTab = 'summary'">Summary</button>
        <button :class="{ active: summaryTab === 'shiftSummary' }" @click="openSummaryShiftTab">Shift Summary</button>
        <button :class="{ active: summaryTab === 'linked' }" @click="summaryTab = 'linked'">Linked Orders</button>
      </div>
      <div v-show="summaryTab === 'summary'" class="gpe-summary-panels">
        <div class="gpe-panel gpe-card-inner">
          <h4>Linked Orders{{ primaryGsmLabel }}</h4>
          <table>
            <thead>
              <tr><th>Order</th><th>Party</th><th>Required</th><th>Produced</th><th>Remaining</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in linkedOrderSummary" :key="r.orderCode">
                <td>{{ r.orderCode }}</td>
                <td>{{ r.partyName }}</td>
                <td>{{ formatKg(r.required) }}</td>
                <td>{{ formatKg(r.produced) }}</td>
                <td>{{ formatKg(r.remaining) }}</td>
              </tr>
              <tr class="total">
                <td colspan="2">Total</td>
                <td>{{ formatKg(linkedTotals.required) }}</td>
                <td>{{ formatKg(linkedTotals.produced) }}</td>
                <td>{{ formatKg(linkedTotals.remaining) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="gpe-panel gpe-card-inner">
          <h4>Batch Summary (session)</h4>
          <table>
            <thead>
              <tr><th>Batch</th><th>Rolls</th><th>Total Kg</th><th>Net Kg</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in batchSummary" :key="b.batch_no">
                <td>{{ b.batch_no }}</td>
                <td>{{ b.rolls }}</td>
                <td>{{ formatKg(b.totalGross) }}</td>
                <td>{{ formatKg(b.totalNet) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="gpe-panel gpe-card-inner">
          <h4>GSM-wise Summary</h4>
          <table>
            <thead>
              <tr><th>GSM</th><th>Required</th><th>Produced</th><th>Remaining</th><th>Progress</th></tr>
            </thead>
            <tbody>
              <tr v-for="g in gsmSummary" :key="g.gsm">
                <td>{{ g.gsm }}</td>
                <td>{{ formatKg(g.required) }}</td>
                <td>{{ formatKg(g.produced) }}</td>
                <td>{{ formatKg(g.remaining) }}</td>
                <td>
                  <div class="gpe-progress"><div :style="{ width: g.pct + '%' }"></div></div>
                  {{ g.pct.toFixed(1) }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <p v-if="summaryTab === 'summary'" class="gpe-note">
        Save Row writes to draft SPR on server. Submit Entry imports and submits SPR(s) for selected orders.
      </p>
      <div v-show="summaryTab === 'shiftSummary'" class="gpe-shift-summary-panel">
        <div class="gpe-shift-filters gpe-card-inner">
          <span class="gpe-shift-filter-title">Shift production (Run Date + Shift + Unit)</span>
          <label>Date <input type="date" v-model="summaryShiftDate" /></label>
          <label>
            Shift
            <select v-model="summaryShiftShift">
              <option value="Day Shift">Day Shift</option>
              <option value="Night Shift">Night Shift</option>
            </select>
          </label>
          <label>
            Unit
            <select v-model="summaryShiftUnit">
              <option value="">All fabric units</option>
              <option v-for="u in fabricUnitOptions" :key="'ssu-' + u" :value="u">{{ u }}</option>
            </select>
          </label>
          <button type="button" class="gpe-btn primary" @click="loadSummaryShiftSummary">Refresh</button>
        </div>
        <div v-if="summaryShiftLoading" class="gpe-muted gpe-card-inner">Loading shift summary…</div>
        <div v-else-if="!summaryShiftSummary?.totals?.spr_count" class="gpe-empty-state gpe-card-inner">
          <h3>No production for this shift</h3>
          <p>Draft and submitted SPRs for the selected date, shift, and unit appear here.</p>
        </div>
        <template v-else>
          <div class="gpe-shift-kpi-grid gpe-card-inner gpe-board-animate">
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 0ms">
              <span class="gpe-kpi-label">Session</span>
              <span :class="['gpe-chip', shiftSessionStatusClass(summaryShiftSummary.session_status)]">
                {{ summaryShiftSummary.session_status }}
              </span>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 60ms">
              <span class="gpe-kpi-label">SPRs</span>
              <strong>{{ summaryShiftSummary.totals.submitted_spr_count }} submitted</strong>
              <span v-if="summaryShiftSummary.totals.draft_spr_count" class="gpe-kpi-sub">
                · {{ summaryShiftSummary.totals.draft_spr_count }} draft
              </span>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 120ms">
              <span class="gpe-kpi-label">Rolls</span>
              <strong>{{ summaryShiftSummary.totals.roll_count }}</strong>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 180ms">
              <span class="gpe-kpi-label">Net Kg</span>
              <strong>{{ formatKg(summaryShiftSummary.totals.net_kg) }}</strong>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 240ms">
              <span class="gpe-kpi-label">Gross Kg</span>
              <strong>{{ formatKg(summaryShiftSummary.totals.gross_kg) }}</strong>
            </div>
          </div>
          <div class="gpe-summary-panels gpe-shift-summary-cards gpe-board-animate">
            <div class="gpe-panel gpe-card-inner gpe-board-card" style="--gpe-delay: 80ms">
              <h4>By Order</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>Order</th><th>Status</th><th>Rolls</th><th>Net Kg</th><th>Gross Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in summaryShiftSummary.by_order" :key="row.order_codes?.join('-') || row.spr_name">
                    <td>{{ row.order_codes?.join(", ") || "—" }}</td>
                    <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                    <td>{{ formatKg(row.gross_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
            <div class="gpe-panel gpe-card-inner gpe-board-card" style="--gpe-delay: 160ms">
              <h4>By GSM</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>GSM</th><th>Rolls</th><th>Net Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in summaryShiftSummary.by_gsm" :key="row.gsm">
                    <td>{{ row.gsm }}</td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
            <div class="gpe-panel gpe-card-inner gpe-board-card" style="--gpe-delay: 240ms">
              <h4>By Batch Series</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>Batch</th><th>Rolls</th><th>Net Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in summaryShiftSummary.by_batch_series" :key="row.batch_series">
                    <td>{{ row.batch_series }}</td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
          </div>
          <div class="gpe-panel wide gpe-card-inner">
            <h4>All SPRs (submitted + draft)</h4>
            <div class="gpe-table-wrap">
            <table>
              <thead>
                <tr><th>SPR</th><th>Status</th><th>Orders</th><th>Rolls</th><th>Net Kg</th><th>Operator</th></tr>
              </thead>
              <tbody>
                <tr v-for="spr in summaryShiftSummary.spr_list" :key="spr.spr_name">
                  <td><a href="#" @click.prevent="openSpr(spr.spr_name)">{{ spr.spr_name }}</a></td>
                  <td><span :class="['gpe-chip', sprStatusChipClass(spr.spr_status)]">{{ spr.spr_status }}</span></td>
                  <td>{{ spr.order_codes?.join(", ") || "—" }}</td>
                  <td>{{ spr.roll_count }}</td>
                  <td>{{ formatKg(spr.total_net_kg) }}</td>
                  <td>{{ spr.operator || "—" }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
          <div class="gpe-shift-roll-section gpe-card-inner">
            <div class="gpe-shift-roll-head">
              <h4>Shift roll entries</h4>
              <span class="gpe-roll-count-badge">{{ shiftRollLines(summaryShiftSummary).length }} rolls</span>
              <span v-if="summaryShiftSummary.roll_lines_truncated" class="gpe-muted gpe-trunc-hint">First 500 rows shown</span>
            </div>
            <div class="gpe-grid-wrap gpe-grid-wrap-shift">
              <table class="gpe-grid gpe-grid-entry gpe-grid-readonly">
                <thead>
                  <tr>
                    <th class="gpe-sticky-col gpe-sticky-0">#</th>
                    <th class="gpe-sticky-col gpe-sticky-1">Order</th>
                    <th class="gpe-num">Job</th>
                    <th>Quality</th>
                    <th>Color</th>
                    <th class="gpe-num">GSM</th>
                    <th class="gpe-num">Width</th>
                    <th class="gpe-num">Ord MTR</th>
                    <th class="gpe-num">Prod MTR</th>
                    <th>Prod GSM</th>
                    <th>Batch</th>
                    <th>Net</th>
                    <th>Gross</th>
                    <th>Planned</th>
                    <th>SPR</th>
                    <th>Status</th>
                    <th>WO</th>
                    <th>Core</th>
                    <th class="gpe-num">Core Base Wt (Kg)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(row, sidx) in shiftRollLines(summaryShiftSummary)"
                    :key="(row.batch_no || '') + '-' + sidx"
                    :class="rowBandClass(row)"
                  >
                    <td class="gpe-sticky-col gpe-sticky-0">{{ shiftRollLines(summaryShiftSummary).length - sidx }}</td>
                    <td class="gpe-sticky-col gpe-sticky-1">{{ row.party_code || "—" }}</td>
                    <td class="gpe-num">{{ row.job_id || "—" }}</td>
                    <td>{{ row.quality || "—" }}</td>
                    <td>{{ row.color || "—" }}</td>
                    <td class="gpe-num">{{ row.gsm }}</td>
                    <td class="gpe-num">{{ widthDisplay(row) }}</td>
                    <td class="gpe-num">{{ row.meter_roll }}</td>
                    <td class="gpe-num">{{ formatMtrs(row.produced_length_mtrs) }}</td>
                    <td>{{ row.produced_gsm }}</td>
                    <td>{{ row.batch_no }}</td>
                    <td>{{ formatKg(row.net_weight) }}</td>
                    <td>{{ formatKg(sprNormalizeGrossWeightInput(row.gross_weight)) }}</td>
                    <td>{{ formatKg(row.planned_qty) }}</td>
                    <td><a href="#" @click.prevent="openSpr(row.spr_name)">{{ row.spr_name }}</a></td>
                    <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                    <td>{{ row.work_order || "—" }}</td>
                    <td>{{ row.custom_core_width_mm || "—" }}</td>
                    <td class="gpe-num">{{ coreBaseWeightDisplay(row) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>
      <div v-show="summaryTab === 'linked'" class="gpe-panel wide gpe-card-inner">
        <table>
          <thead>
            <tr><th>Order</th><th>Party</th><th>GSM</th><th>Width</th><th>Required</th><th>Session Kg</th><th>PT Achieved</th></tr>
          </thead>
          <tbody>
            <tr v-for="line in selectedLinesDetail" :key="line.id">
              <td>{{ line.orderCode }}</td>
              <td>{{ line.partyName }}</td>
              <td>{{ line.gsm }}</td>
              <td>{{ line.width_inch }}</td>
              <td>{{ formatKg(line.requiredKg) }}</td>
              <td>{{ formatKg(line.sessionKg) }}</td>
              <td>{{ formatKg(line.achievedKg) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Shift Entries tab -->
    <div v-show="pageTab === 'shift'" class="gpe-shift-layout">
      <div class="gpe-shift-filters gpe-card">
        <span class="gpe-shift-filter-title">Shift production</span>
        <label>Date <input type="date" v-model="shiftFilterDate" /></label>
        <label>
          Shift
          <select v-model="shiftFilterShift">
            <option value="Day Shift">Day Shift</option>
            <option value="Night Shift">Night Shift</option>
          </select>
        </label>
        <label>
          Unit
          <select v-model="shiftFilterUnit">
            <option value="">All fabric units</option>
            <option v-for="u in fabricUnitOptions" :key="'su-' + u" :value="u">{{ u }}</option>
          </select>
        </label>
        <div class="gpe-shift-view-toggle">
          <button
            type="button"
            :class="{ active: shiftEntriesView === 'spr' }"
            @click="shiftEntriesView = 'spr'"
          >SPR-wise</button>
          <button
            type="button"
            :class="{ active: shiftEntriesView === 'consolidated' }"
            @click="shiftEntriesView = 'consolidated'"
          >Shift summary</button>
        </div>
        <button type="button" class="gpe-btn primary" @click="loadShiftEntries">Refresh</button>
      </div>

      <div v-if="shiftLoading" class="gpe-muted gpe-card">Loading shift entries…</div>
      <template v-else-if="shiftEntriesView === 'consolidated'">
        <div v-if="!shiftConsolidated?.totals?.spr_count" class="gpe-empty-state gpe-card">
          <h3>No production for this shift</h3>
          <p>Draft and submitted SPRs for the selected date, shift, and unit appear here.</p>
        </div>
        <div v-else class="gpe-shift-consolidated">
          <div class="gpe-shift-kpi-grid gpe-card gpe-board-animate">
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 0ms">
              <span class="gpe-kpi-label">Session</span>
              <span :class="['gpe-chip', shiftSessionStatusClass(shiftConsolidated.session_status)]">
                {{ shiftConsolidated.session_status }}
              </span>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 60ms">
              <span class="gpe-kpi-label">SPRs</span>
              <strong>{{ shiftConsolidated.totals.submitted_spr_count }} submitted</strong>
              <span v-if="shiftConsolidated.totals.draft_spr_count" class="gpe-kpi-sub">
                · {{ shiftConsolidated.totals.draft_spr_count }} draft
              </span>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 120ms">
              <span class="gpe-kpi-label">Rolls</span>
              <strong>{{ shiftConsolidated.totals.roll_count }}</strong>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 180ms">
              <span class="gpe-kpi-label">Net Kg</span>
              <strong>{{ formatKg(shiftConsolidated.totals.net_kg) }}</strong>
            </div>
            <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 240ms">
              <span class="gpe-kpi-label">Gross Kg</span>
              <strong>{{ formatKg(shiftConsolidated.totals.gross_kg) }}</strong>
            </div>
          </div>
          <div class="gpe-summary-panels gpe-shift-summary-cards gpe-board-animate">
            <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 80ms">
              <h4>By Order</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>Order</th><th>Status</th><th>Rolls</th><th>Net Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in shiftConsolidated.by_order" :key="row.order_codes?.join('-') || row.spr_name">
                    <td>{{ row.order_codes?.join(", ") || "—" }}</td>
                    <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
            <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 160ms">
              <h4>By GSM</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>GSM</th><th>Rolls</th><th>Net Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in shiftConsolidated.by_gsm" :key="row.gsm">
                    <td>{{ row.gsm }}</td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
            <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 240ms">
              <h4>By Batch Series</h4>
              <div class="gpe-table-wrap">
              <table>
                <thead>
                  <tr><th>Batch</th><th>Rolls</th><th>Net Kg</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in shiftConsolidated.by_batch_series" :key="row.batch_series">
                    <td>{{ row.batch_series }}</td>
                    <td>{{ row.rolls }}</td>
                    <td>{{ formatKg(row.net_kg) }}</td>
                  </tr>
                </tbody>
              </table>
              </div>
            </div>
          </div>
          <div class="gpe-panel wide gpe-card gpe-board-card">
            <h4>All SPRs (submitted + draft)</h4>
            <div class="gpe-table-wrap">
            <table>
              <thead>
                <tr><th>SPR</th><th>Status</th><th>Orders</th><th>Rolls</th><th>Net Kg</th><th>Operator</th></tr>
              </thead>
              <tbody>
                <tr v-for="spr in shiftConsolidated.spr_list" :key="spr.spr_name">
                  <td><a href="#" @click.prevent="openSpr(spr.spr_name)">{{ spr.spr_name }}</a></td>
                  <td><span :class="['gpe-chip', sprStatusChipClass(spr.spr_status)]">{{ spr.spr_status }}</span></td>
                  <td>{{ spr.order_codes?.join(", ") || "—" }}</td>
                  <td>{{ spr.roll_count }}</td>
                  <td>{{ formatKg(spr.total_net_kg) }}</td>
                  <td>{{ spr.operator || "—" }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
          <div class="gpe-shift-roll-section gpe-card">
            <div class="gpe-shift-roll-head">
              <h4>Shift roll entries</h4>
              <span class="gpe-roll-count-badge">{{ shiftRollLines(shiftConsolidated).length }} rolls</span>
              <span v-if="shiftConsolidated.roll_lines_truncated" class="gpe-muted gpe-trunc-hint">First 500 rows shown</span>
            </div>
            <div class="gpe-grid-wrap gpe-grid-wrap-shift">
              <table class="gpe-grid gpe-grid-entry gpe-grid-readonly">
                <thead>
                  <tr>
                    <th class="gpe-sticky-col gpe-sticky-0">#</th>
                    <th class="gpe-sticky-col gpe-sticky-1">Order</th>
                    <th class="gpe-num">Job</th>
                    <th>Quality</th>
                    <th>Color</th>
                    <th class="gpe-num">GSM</th>
                    <th class="gpe-num">Width</th>
                    <th class="gpe-num">Ord MTR</th>
                    <th class="gpe-num">Prod MTR</th>
                    <th>Prod GSM</th>
                    <th>Batch</th>
                    <th>Net</th>
                    <th>Gross</th>
                    <th>Planned</th>
                    <th>SPR</th>
                    <th>Status</th>
                    <th>WO</th>
                    <th>Core</th>
                    <th class="gpe-num">Core Base Wt (Kg)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(row, sidx) in shiftRollLines(shiftConsolidated)"
                    :key="(row.batch_no || '') + '-' + sidx"
                    :class="rowBandClass(row)"
                  >
                    <td class="gpe-sticky-col gpe-sticky-0">{{ shiftRollLines(shiftConsolidated).length - sidx }}</td>
                    <td class="gpe-sticky-col gpe-sticky-1">{{ row.party_code || "—" }}</td>
                    <td class="gpe-num">{{ row.job_id || "—" }}</td>
                    <td>{{ row.quality || "—" }}</td>
                    <td>{{ row.color || "—" }}</td>
                    <td class="gpe-num">{{ row.gsm }}</td>
                    <td class="gpe-num">{{ widthDisplay(row) }}</td>
                    <td class="gpe-num">{{ row.meter_roll }}</td>
                    <td class="gpe-num">{{ formatMtrs(row.produced_length_mtrs) }}</td>
                    <td>{{ row.produced_gsm }}</td>
                    <td>{{ row.batch_no }}</td>
                    <td>{{ formatKg(row.net_weight) }}</td>
                    <td>{{ formatKg(sprNormalizeGrossWeightInput(row.gross_weight)) }}</td>
                    <td>{{ formatKg(row.planned_qty) }}</td>
                    <td><a href="#" @click.prevent="openSpr(row.spr_name)">{{ row.spr_name }}</a></td>
                    <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                    <td>{{ row.work_order || "—" }}</td>
                    <td>{{ row.custom_core_width_mm || "—" }}</td>
                    <td class="gpe-num">{{ coreBaseWeightDisplay(row) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="!shiftEntries.length" class="gpe-empty-state gpe-card">
        <h3>No submitted entries for this shift</h3>
        <p>Submitted SPRs for the selected date, shift, and unit appear here.</p>
      </div>
      <div v-else class="gpe-shift-split">
        <aside class="gpe-shift-sidebar gpe-card">
          <div
            v-for="entry in shiftEntries"
            :key="entry.spr_name"
            class="gpe-shift-card"
            :class="{ active: selectedShiftEntry?.spr_name === entry.spr_name }"
            @click="selectedShiftEntry = entry"
          >
            <div class="gpe-shift-card-title">{{ entry.spr_name }}</div>
            <div class="gpe-shift-card-meta">
              {{ entry.order_codes?.join(", ") || "—" }}
            </div>
            <div class="gpe-shift-card-stats">
              {{ entry.roll_count }} rolls · {{ formatKg(entry.total_net_kg) }} Kg net
            </div>
            <span class="gpe-chip gpe-chip-submitted">Submitted</span>
          </div>
        </aside>
        <div v-if="selectedShiftEntry" class="gpe-shift-detail gpe-card">
          <div class="gpe-shift-detail-head">
            <h3>{{ selectedShiftEntry.spr_name }}</h3>
            <button type="button" class="gpe-btn sm" @click="openSpr(selectedShiftEntry.spr_name)">Open SPR</button>
          </div>
          <p class="gpe-shift-meta">
            PP: <a href="#" @click.prevent="viewPP(selectedShiftEntry.production_plan)">{{ selectedShiftEntry.production_plan }}</a>
            · Operator: {{ selectedShiftEntry.operator || "—" }}
            · Supervisor: {{ selectedShiftEntry.supervisor || "—" }}
          </p>
          <div v-if="selectedShiftEntry.wo_status?.length" class="gpe-wo-chips">
            <span
              v-for="wo in selectedShiftEntry.wo_status"
              :key="wo.name"
              :class="['gpe-chip', wo.status === 'Completed' ? 'gpe-chip-done' : 'gpe-chip-draft']"
            >{{ wo.name }} ({{ wo.status }})</span>
          </div>
          <table class="gpe-grid">
            <thead>
              <tr>
                <th>Batch</th><th>GSM</th><th>Width</th><th>Net</th><th>Gross</th><th>Order</th><th>WO</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in selectedShiftEntry.rolls" :key="i">
                <td>{{ r.batch_no }}</td>
                <td>{{ r.gsm }}</td>
                <td>{{ r.width_inch }}</td>
                <td>{{ formatKg(r.net_weight) }}</td>
                <td>{{ formatKg(r.gross_weight) }}</td>
                <td>{{ r.party_code }}</td>
                <td>{{ r.work_order }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="gpe-shift-detail gpe-card gpe-muted">Select an entry to view rolls.</div>
      </div>
    </div>

    <!-- Prev Shift tab (full shift-wise consolidated view, read-only) -->
    <div v-show="pageTab === 'prevshift'" class="gpe-shift-layout">
      <div class="gpe-shift-filters gpe-card">
        <span class="gpe-shift-filter-title">Previous Shift</span>
        <span class="gpe-prev-shift-badge">{{ prevShiftLabel }} — Read Only</span>
        <button type="button" class="gpe-btn primary" @click="loadPrevShiftConsolidated">Refresh</button>
      </div>

      <div v-if="prevShiftLoading" class="gpe-muted gpe-card">Loading previous shift data…</div>
      <div v-else-if="!prevShiftConsolidated?.totals?.spr_count" class="gpe-empty-state gpe-card">
        <h3>No production for previous shift</h3>
        <p>{{ prevShiftLabel }}</p>
      </div>
      <div v-else class="gpe-shift-consolidated">
        <div class="gpe-shift-kpi-grid gpe-card gpe-board-animate">
          <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 0ms">
            <span class="gpe-kpi-label">Session</span>
            <span :class="['gpe-chip', shiftSessionStatusClass(prevShiftConsolidated.session_status)]">
              {{ prevShiftConsolidated.session_status }}
            </span>
          </div>
          <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 60ms">
            <span class="gpe-kpi-label">SPRs</span>
            <strong>{{ prevShiftConsolidated.totals.submitted_spr_count }} submitted</strong>
            <span v-if="prevShiftConsolidated.totals.draft_spr_count" class="gpe-kpi-sub">
              · {{ prevShiftConsolidated.totals.draft_spr_count }} draft
            </span>
          </div>
          <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 120ms">
            <span class="gpe-kpi-label">Rolls</span>
            <strong>{{ prevShiftConsolidated.totals.roll_count }}</strong>
          </div>
          <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 180ms">
            <span class="gpe-kpi-label">Net Kg</span>
            <strong>{{ formatKg(prevShiftConsolidated.totals.net_kg) }}</strong>
          </div>
          <div class="gpe-kpi gpe-board-card" style="--gpe-delay: 240ms">
            <span class="gpe-kpi-label">Gross Kg</span>
            <strong>{{ formatKg(prevShiftConsolidated.totals.gross_kg) }}</strong>
          </div>
        </div>
        <div class="gpe-summary-panels gpe-shift-summary-cards gpe-board-animate">
          <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 80ms">
            <h4>By Order</h4>
            <div class="gpe-table-wrap">
            <table>
              <thead>
                <tr><th>Order</th><th>Status</th><th>Rolls</th><th>Net Kg</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in prevShiftConsolidated.by_order" :key="row.order_codes?.join('-') || row.spr_name">
                  <td>{{ row.order_codes?.join(", ") || "—" }}</td>
                  <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                  <td>{{ row.rolls }}</td>
                  <td>{{ formatKg(row.net_kg) }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
          <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 160ms">
            <h4>By GSM</h4>
            <div class="gpe-table-wrap">
            <table>
              <thead>
                <tr><th>GSM</th><th>Rolls</th><th>Net Kg</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in prevShiftConsolidated.by_gsm" :key="row.gsm">
                  <td>{{ row.gsm }}</td>
                  <td>{{ row.rolls }}</td>
                  <td>{{ formatKg(row.net_kg) }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
          <div class="gpe-panel gpe-card gpe-board-card" style="--gpe-delay: 240ms">
            <h4>By Batch Series</h4>
            <div class="gpe-table-wrap">
            <table>
              <thead>
                <tr><th>Batch</th><th>Rolls</th><th>Net Kg</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in prevShiftConsolidated.by_batch_series" :key="row.batch_series">
                  <td>{{ row.batch_series }}</td>
                  <td>{{ row.rolls }}</td>
                  <td>{{ formatKg(row.net_kg) }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
        </div>
        <div class="gpe-panel wide gpe-card gpe-board-card">
          <h4>All SPRs (submitted + draft)</h4>
          <div class="gpe-table-wrap">
          <table>
            <thead>
              <tr><th>SPR</th><th>Status</th><th>Orders</th><th>Rolls</th><th>Net Kg</th><th>Operator</th></tr>
            </thead>
            <tbody>
              <tr v-for="spr in prevShiftConsolidated.spr_list" :key="spr.spr_name">
                <td><a href="#" @click.prevent="openSpr(spr.spr_name)">{{ spr.spr_name }}</a></td>
                <td><span :class="['gpe-chip', sprStatusChipClass(spr.spr_status)]">{{ spr.spr_status }}</span></td>
                <td>{{ spr.order_codes?.join(", ") || "—" }}</td>
                <td>{{ spr.roll_count }}</td>
                <td>{{ formatKg(spr.total_net_kg) }}</td>
                <td>{{ spr.operator || "—" }}</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
        <div class="gpe-shift-roll-section gpe-card">
          <div class="gpe-shift-roll-head">
            <h4>Shift roll entries</h4>
            <span class="gpe-roll-count-badge">{{ shiftRollLines(prevShiftConsolidated).length }} rolls</span>
            <span v-if="prevShiftConsolidated.roll_lines_truncated" class="gpe-muted gpe-trunc-hint">First 500 rows shown</span>
          </div>
          <div class="gpe-grid-wrap gpe-grid-wrap-shift">
            <table class="gpe-grid gpe-grid-entry gpe-grid-readonly">
              <thead>
                <tr>
                  <th class="gpe-sticky-col gpe-sticky-0">#</th>
                  <th class="gpe-sticky-col gpe-sticky-1">Order</th>
                  <th class="gpe-num">Job</th>
                  <th>Quality</th>
                  <th>Color</th>
                  <th class="gpe-num">GSM</th>
                  <th class="gpe-num">Width</th>
                  <th class="gpe-num">Ord MTR</th>
                  <th class="gpe-num">Prod MTR</th>
                  <th>Prod GSM</th>
                  <th>Batch</th>
                  <th>Net</th>
                  <th>Gross</th>
                  <th>Planned</th>
                  <th>SPR</th>
                  <th>Status</th>
                  <th>WO</th>
                  <th>Core</th>
                  <th class="gpe-num">Core Base Wt (Kg)</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, sidx) in shiftRollLines(prevShiftConsolidated)"
                  :key="(row.batch_no || '') + '-prev-' + sidx"
                  :class="rowBandClass(row)"
                >
                  <td class="gpe-sticky-col gpe-sticky-0">{{ shiftRollLines(prevShiftConsolidated).length - sidx }}</td>
                  <td class="gpe-sticky-col gpe-sticky-1">{{ row.party_code || "—" }}</td>
                  <td class="gpe-num">{{ row.job_id || "—" }}</td>
                  <td>{{ row.quality || "—" }}</td>
                  <td>{{ row.color || "—" }}</td>
                  <td class="gpe-num">{{ row.gsm }}</td>
                  <td class="gpe-num">{{ widthDisplay(row) }}</td>
                  <td class="gpe-num">{{ row.meter_roll }}</td>
                  <td class="gpe-num">{{ formatMtrs(row.produced_length_mtrs) }}</td>
                  <td>{{ row.produced_gsm }}</td>
                  <td>{{ row.batch_no }}</td>
                  <td>{{ formatKg(row.net_weight) }}</td>
                  <td>{{ formatKg(sprNormalizeGrossWeightInput(row.gross_weight)) }}</td>
                  <td>{{ formatKg(row.planned_qty) }}</td>
                  <td><a href="#" @click.prevent="openSpr(row.spr_name)">{{ row.spr_name }}</a></td>
                  <td><span :class="['gpe-chip', sprStatusChipClass(row.spr_status)]">{{ row.spr_status }}</span></td>
                  <td>{{ row.work_order || "—" }}</td>
                  <td>{{ row.custom_core_width_mm || "—" }}</td>
                  <td class="gpe-num">{{ coreBaseWeightDisplay(row) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Shift open dialog -->
    <div v-if="showShiftOpenDialog" class="gpe-dialog-overlay" @click.self="closeShiftDialog">
      <div class="gpe-dialog gpe-card gpe-shift-open-dialog">
        <h3>Open {{ shift }}</h3>
        <p class="gpe-hint">Start this shift before selecting jobs or entering rolls.</p>
        <div v-if="shiftReopenRequired" class="gpe-reopen-notice">
          This shift was closed earlier (batch <strong>{{ shiftReopenClosedBatch }}</strong>).
          A new batch will be allocated. Please state why you are re-opening.
        </div>
        <div v-else-if="shiftBatchReuseNotice" class="gpe-reopen-notice gpe-reuse-notice">
          {{ shiftBatchReuseNotice }}
        </div>
        <div class="gpe-shift-open-fields">
          <label class="gpe-emp-link">
            Operator
            <div class="gpe-emp-link-row">
              <input :value="operator" type="text" readonly placeholder="Select employee" />
              <button type="button" class="gpe-btn" @click="pickEmployeeLink('operator', 'Operator')">Pick</button>
            </div>
          </label>
          <label class="gpe-emp-link">
            Supervisor
            <div class="gpe-emp-link-row">
              <input :value="supervisor" type="text" readonly placeholder="Select employee" />
              <button type="button" class="gpe-btn" @click="pickEmployeeLink('supervisor', 'Supervisor')">Pick</button>
            </div>
          </label>
          <label class="gpe-emp-link">
            Co-ordinator
            <div class="gpe-emp-link-row">
              <input :value="coordinator" type="text" readonly placeholder="Select employee" />
              <button type="button" class="gpe-btn" @click="pickEmployeeLink('coordinator', 'Co-ordinator')">Pick</button>
            </div>
          </label>
          <div v-if="shiftReopenRequired" class="gpe-shift-reopen-fields">
            <label>
              Re-open reason
              <select v-model="shiftReopenReason">
                <option value="">Select reason…</option>
                <option v-for="r in gsmReopenReasons" :key="r" :value="r">{{ r }}</option>
              </select>
            </label>
            <label>
              Remarks
              <textarea
                v-model="shiftReopenRemarks"
                rows="2"
                :placeholder="shiftReopenReason === 'Other' ? 'Required for Other' : 'Optional'"
              ></textarea>
            </label>
          </div>
          <div class="gpe-shift-batch-preview">
            <span class="gpe-shift-batch-label">Shift batch</span>
            <strong class="gpe-batch-badge">{{ shiftPreviewBatch || "…" }}</strong>
          </div>
        </div>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn" @click="closeShiftDialog">Cancel</button>
          <button
            type="button"
            class="gpe-btn primary"
            :disabled="shiftOpeningBusy || !canConfirmShiftOpen"
            @click="startShift"
          >
            {{ shiftOpeningBusy ? "Starting…" : "Start Shift" }}
          </button>
        </div>
      </div>
    </div>

    <!-- Submit entry confirm -->
    <div
      v-if="showSubmitConfirmDialog"
      class="gpe-dialog-overlay"
      @click.self="submitDialogPhase === 'review' ? closeSubmitDialog() : null"
    >
      <div class="gpe-dialog gpe-card gpe-submit-confirm-dialog">
        <template v-if="submitDialogPhase === 'review'">
          <h3>Submit entry — review</h3>
          <p class="gpe-hint">
            {{ shift }} · {{ formatPlannedDate(runDate) }} · Batch {{ shiftBatchPrefix || seriesPrefix || "—" }}
            · Operator {{ operator || "—" }} · Supervisor {{ supervisor || "—" }} · Co-ordinator {{ coordinator || "—" }}
          </p>
          <div class="gpe-submit-summary-totals">
            <span><strong>{{ submitConfirmRolls.length }}</strong> roll(s)</span>
            <span>Net <strong>{{ formatKg(submitConfirmNetKg) }}</strong> Kg</span>
            <span>Gross <strong>{{ formatKg(submitConfirmGrossKg) }}</strong> Kg</span>
          </div>
          <p v-if="submitIncompleteRolls.length" class="gpe-hint gpe-submit-skip-hint">
            {{ submitIncompleteRolls.length }} grid row(s) not included (missing produced length, gross weight, or incomplete):
            {{ submitIncompleteRolls.map((r) => r.batch_no || r._id).join(", ") }}
          </p>
          <p
            v-if="!allSubmitRollsSaved"
            class="gpe-hint gpe-submit-skip-hint"
            style="color:#b45309"
          >
            Unsaved rolls must be Save Row'd before submit — they will not appear on Shaft Production Run / wastage until saved.
          </p>
          <h4 class="gpe-submit-section-title">Orders</h4>
          <table class="gpe-confirm-grid">
            <thead>
              <tr>
                <th>Order</th><th>Net Kg (submitting)</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="o in submitOrderSummary" :key="o.orderCode">
                <td>{{ o.orderCode }}</td>
                <td>{{ formatKg(o.produced) }}</td>
              </tr>
            </tbody>
          </table>
          <h4 class="gpe-submit-section-title">Rolls</h4>
          <table class="gpe-confirm-grid">
            <thead>
              <tr>
                <th>Batch</th><th>Width</th><th>Order</th><th>Job</th><th>Net Kg</th><th>Gross Kg</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in submitConfirmRolls" :key="r._id">
                <td>{{ r.batch_no }}</td>
                <td>{{ widthDisplay(r) }}</td>
                <td>{{ r.party_code }}</td>
                <td>{{ r.job_id || r.job }}</td>
                <td>{{ formatKg(r.net_weight) }}</td>
                <td>{{ formatKg(sprNormalizeGrossWeightInput(r.gross_weight)) }}</td>
              </tr>
            </tbody>
          </table>
          <h4 class="gpe-submit-section-title">SPRs</h4>
          <ul class="gpe-submit-spr-list">
            <li v-for="s in allSubmitSprList" :key="s.spr_name">
              {{ s.order_code }} · {{ s.label_type || "Default" }} · {{ s.spr_name }}
            </li>
          </ul>
          <p class="gpe-hint">Submit {{ submitConfirmRolls.length }} roll(s) across {{ allSubmitSprList.length }} SPR(s)?</p>
          <div class="gpe-dialog-actions">
            <button type="button" class="gpe-btn" @click="closeSubmitDialog">No, stay here</button>
            <button type="button" class="gpe-btn primary" @click="confirmSubmitEntry">Yes, submit entry</button>
          </div>
        </template>

        <template v-else-if="submitDialogPhase === 'submitting'">
          <h3>Submitting entry…</h3>
          <div class="gpe-submit-progress">
            <div class="gpe-submit-spinner"></div>
            <p class="gpe-submit-progress-msg">{{ submitProgressMessage }}</p>
            <p class="gpe-hint">Do not reload or close this window.</p>
          </div>
        </template>

        <template v-else-if="submitDialogPhase === 'success'">
          <h3 class="gpe-submit-success-title">Successfully submitted</h3>
          <p class="gpe-submit-success-msg">
            {{ submitSuccessResult?.count || 0 }} SPR(s) submitted
            · {{ submitSuccessResult?.rollCount || submitConfirmRolls.length }} roll(s)
            <span v-if="submitSuccessResult?.totalKg"> · {{ formatKg(submitSuccessResult.totalKg) }} Kg net</span>
          </p>
          <ul v-if="submitSuccessResult?.sprNames?.length" class="gpe-submit-spr-list">
            <li v-for="sn in submitSuccessResult.sprNames" :key="sn">
              <a href="#" @click.prevent="openSpr(sn)">{{ sn }}</a>
            </li>
          </ul>
          <div class="gpe-dialog-actions">
            <button type="button" class="gpe-btn primary" @click="closeSubmitDialog">OK</button>
          </div>
        </template>

        <template v-else-if="submitDialogPhase === 'error'">
          <h3>Submit could not be confirmed</h3>
          <p class="gpe-submit-error-msg">{{ submitErrorMessage }}</p>
          <div class="gpe-dialog-actions">
            <button type="button" class="gpe-btn primary" @click="closeSubmitDialog">Stay on entry</button>
          </div>
        </template>
      </div>
    </div>

    <!-- Shift ending reminder -->
    <div v-if="showShiftReminder" class="gpe-dialog-overlay">
      <div class="gpe-dialog gpe-card">
        <h3>Shift ending reminder</h3>
        <p>It is time to close the shift. You will be redirected to Shift Wise Production Entry.</p>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn" @click="dismissShiftReminder">Later</button>
          <button type="button" class="gpe-btn primary" @click="closeShiftFromReminder">Go to Shift Wise Entry</button>
        </div>
      </div>
    </div>

    <!-- Confirm selection dialog -->
    <div v-if="showConfirmDialog" class="gpe-dialog-overlay" @click.self="showConfirmDialog = false">
      <div class="gpe-dialog gpe-card">
        <h3>Confirm job selection</h3>
        <p>Lock these jobs for roll entry? You can unlock later.</p>
        <table class="gpe-confirm-grid">
          <thead>
            <tr v-if="isLaminationMode">
              <th>Order</th><th>Job</th><th>Quality</th><th>Color</th><th>GSM</th><th>Width</th><th>Progress</th>
            </tr>
            <tr v-else>
              <th>Order</th><th>Job</th><th>GSM</th><th>Combination</th><th>Progress</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="line in confirmLines" :key="line.key">
              <td>{{ line.orderCode }}</td>
              <td>{{ line.jobId || line.job_id }}</td>
              <template v-if="isLaminationMode">
                <td>{{ line.quality || "—" }}</td>
                <td>{{ line.color || "—" }}</td>
                <td>{{ confirmLineGsm(line) }}</td>
                <td>{{ confirmLineWidth(line) }}</td>
              </template>
              <template v-else>
                <td>{{ line.gsm }}</td>
                <td>{{ line.combination_label || line.widthLabel }}</td>
              </template>
              <td>{{ confirmLineProgress(line) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn" @click="showConfirmDialog = false">Cancel</button>
          <button type="button" class="gpe-btn primary" @click="confirmSelection">Lock selection</button>
        </div>
      </div>
    </div>

    <!-- Add Roll wizard: Job → Width -->
    <div v-if="showAddRollWizard" class="gpe-dialog-overlay" @click.self="cancelAddRollWizard">
      <div class="gpe-dialog gpe-card gpe-add-roll-wizard">
        <h3>{{ addRollWizardStep === 1 ? "Choose order for new roll" : "Choose width" }}</h3>
        <div v-if="addRollWizardStep === 1 && !addRollWizardSkipJobStep" class="gpe-picker-list">
          <label
            v-for="entry in wizardJobChoices"
            :key="entry.key"
            class="gpe-picker-row"
            :class="{ 'gpe-picker-maxed': entry.maxed }"
          >
            <input v-model="addRollJobChoice" type="radio" :value="entry.key" :disabled="entry.maxed && entry.is_mix" />
            <span>
              <template v-if="entry.is_mix">
                {{ entry.orderCode }} · Mix · {{ entry.gsm }} GSM
                <em class="gpe-picker-sub">
                  {{ entry.shaft || "" }}
                  <strong v-if="entry.maxed" class="gpe-picker-maxed-tag"> · MAX</strong>
                </em>
              </template>
              <template v-else>
              {{ entry.orderCode }} · Job {{ entry.jobId || entry.job_id }} · {{ entry.gsm }} GSM
              <strong v-if="entry.is_manual" class="gpe-picker-manual-tag"> · Manual</strong>
              <em v-if="entry.board" class="gpe-picker-sub">
                {{ entry.board.job_shafts_produced }}/{{ entry.board.max_shafts }} shafts ·
                {{ entry.board.job_rolls_produced }}/{{ entry.board.max_rolls }} rolls
                <strong v-if="entry.maxed" class="gpe-picker-maxed-tag"> · MAX — Manual Job only</strong>
              </em>
              </template>
            </span>
          </label>
        </div>
        <div v-else class="gpe-picker-list">
          <div v-if="wizardSelectedJobMaxed" class="gpe-picker-maxed-note">
            This job reached its planned max rolls
            ({{ wizardSelectedJob?.job_rolls_produced }}/{{ wizardSelectedJob?.max_rolls }}).
            Extra production must go through Manual Job.
          </div>
          <label v-for="seg in wizardWidthSegments" :key="seg.width_inch" class="gpe-picker-row" :class="{ 'gpe-picker-disabled': !seg.can_add }">
            <input
              v-model="addRollWidthChoice"
              type="radio"
              :value="seg.width_inch"
              :disabled="!seg.can_add"
            />
            <span>
              {{ seg.width_inch }}" · {{ seg.current }}/{{ seg.max }} rolls
              <em v-if="!seg.can_add" class="gpe-picker-sub">(full)</em>
            </span>
          </label>
        </div>
        <div class="gpe-dialog-actions">
          <button
            type="button"
            class="gpe-btn"
            @click="addRollWizardStep === 1 || addRollWizardSkipJobStep ? cancelAddRollWizard() : (addRollWizardStep = 1)"
          >
            {{ addRollWizardStep === 1 || addRollWizardSkipJobStep ? "Cancel" : "Back" }}
          </button>
          <button
            v-if="wizardSelectedJobMaxed"
            type="button"
            class="gpe-btn warn"
            @click="openManualJobFromWizard"
          >Manual Job</button>
          <button
            v-else
            type="button"
            class="gpe-btn primary"
            :disabled="addRollWizardStep === 1 ? !addRollJobChoice : addRollWidthChoice == null"
            @click="proceedAddRollWizard"
          >{{ addRollJobChoice === MIX_WIZARD_KEY || addRollWizardStep === 2 ? "Add row" : "Next" }}</button>
        </div>
      </div>
    </div>
    <!-- Tolerance approval dialog (multi-order) -->
    <div v-if="showToleranceDialog" class="gpe-dialog-overlay" @click.self="showToleranceDialog = false">
      <div class="gpe-dialog gpe-dialog-wide gpe-card">
        <h3>Tolerance approval required</h3>
        <p class="gpe-muted">{{ toleranceOrders.length }} order(s) need approval before submit.</p>
        <div v-for="order in toleranceOrders" :key="order.spr_name" class="gpe-tol-card">
          <div class="gpe-tol-card-head">
            <strong>{{ order.order_code || "—" }}</strong>
            <span>{{ order.spr_name }}</span>
          </div>
          <table class="gpe-grid gpe-tol-table">
            <thead>
              <tr><th>Job</th><th>Roll</th><th>Planned (Kg)</th><th>Net/Gross (Kg)</th><th>Variance</th></tr>
            </thead>
            <tbody>
              <tr v-for="(v, vi) in order.violations" :key="vi">
                <td>{{ v.job }}</td>
                <td>{{ v.roll_no }}</td>
                <td>{{ formatKg(v.planned) }}</td>
                <td>{{ formatKg(v.actual) }}</td>
                <td>{{ v.dev_pct }}%</td>
              </tr>
            </tbody>
          </table>
          <label class="gpe-tol-reason">
            Reason for override
            <textarea v-model="toleranceForm[order.spr_name].reason" rows="2" />
          </label>
          <label class="gpe-tol-check">
            <input v-model="toleranceForm[order.spr_name].approved" type="checkbox" />
            I approve this deviation
          </label>
        </div>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn" @click="showToleranceDialog = false">Cancel</button>
          <button type="button" class="gpe-btn primary" :disabled="!toleranceFormComplete" @click="submitWithTolerance">Submit all with approval</button>
        </div>
      </div>
    </div>
    <!-- Shaft details dialog -->
    <div v-if="showShaftDetailsDialog" class="gpe-dialog-overlay" @click.self="showShaftDetailsDialog = false">
      <div class="gpe-dialog gpe-dialog-wide gpe-card">
        <h3>Shaft Details</h3>
        <p v-if="shaftDetailsLoading" class="gpe-muted">Loading…</p>
        <div v-for="block in shaftDetailsBlocks" :key="block.pp_id" class="gpe-shaft-block">
          <div class="gpe-shaft-block-head">
            <strong>{{ block.order_code || block.pp_id }}</strong>
            <span v-if="block.label_type">· {{ block.label_type }}</span>
          </div>
          <table v-if="block.shaft_rows?.length" class="gpe-grid gpe-shaft-grid">
            <thead>
              <tr>
                <th>Job</th><th>No of Shafts</th><th>GSM</th><th>Combination</th><th>Total Width</th><th>Meter/Roll</th><th>Net Weight</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, ri) in block.shaft_rows" :key="ri">
                <td>{{ row.job }}</td>
                <td>{{ row.no_of_shafts }}</td>
                <td>{{ row.gsm }}</td>
                <td>{{ row.combination }}</td>
                <td>{{ row.total_width }}</td>
                <td>{{ row.meter_roll }}</td>
                <td>{{ formatNetWeightDisplay(row.net_weight) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="gpe-muted">{{ block.message || "No shaft rows on this PP." }}</p>
        </div>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn primary" @click="showShaftDetailsDialog = false">Close</button>
        </div>
      </div>
    </div>

    <div v-if="showAddMixRollDialog" class="gpe-dialog-overlay" @click.self="closeAddMixRollDialog">
      <div class="gpe-dialog gpe-dialog-wide gpe-card gpe-add-mix-roll-dialog">
        <h3>Add Mix Roll</h3>
        <p class="gpe-hint">
          Saves to Color Chart for this unit, then shows a Mix Rolls card so you can start production.
        </p>
        <div class="gpe-add-mix-roll-grid">
          <label>
            Unit
            <input :value="addMixRollForm.unit" type="text" readonly />
          </label>
          <label>
            Mix Name
            <input v-model="addMixRollForm.mixName" type="text" placeholder="GPKL - …" />
          </label>
          <label>
            From colour
            <select
              v-model="addMixRollForm.color1"
              :style="mixColorBadgeStyle(addMixRollForm.color1)"
              @change="onAddMixRollColorChange"
            >
              <option value="">Select colour</option>
              <option v-for="c in addMixRollColorOptions" :key="'from-' + c" :value="c">{{ c }}</option>
            </select>
          </label>
          <label>
            To colour
            <select
              v-model="addMixRollForm.color2"
              :style="mixColorBadgeStyle(addMixRollForm.color2)"
              @change="onAddMixRollColorChange"
            >
              <option value="">Select colour</option>
              <option v-for="c in addMixRollColorOptions" :key="'to-' + c" :value="c">{{ c }}</option>
            </select>
          </label>
          <label>
            Quality
            <select v-model="addMixRollForm.quality">
              <option v-for="q in MIX_QUALITY_OPTIONS" :key="q" :value="q">{{ q }}</option>
            </select>
          </label>
          <label>
            Color
            <select v-model="addMixRollForm.clType">
              <option v-for="t in MIX_CL_TYPE_OPTIONS" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <label>
            GSM
            <input v-model="addMixRollForm.gsm" type="number" min="1" step="1" placeholder="50" />
          </label>
          <label>
            No of Shaft
            <input v-model.number="addMixRollForm.noOfShaft" type="number" min="1" step="1" />
          </label>
          <label class="gpe-add-mix-roll-span2">
            Shaft Details
            <input v-model="addMixRollForm.shaft" type="text" placeholder="32 + 30..." />
            <span v-if="addMixRollShaftHint" class="gpe-muted gpe-add-mix-roll-hint">{{ addMixRollShaftHint }}</span>
          </label>
          <label>
            Weight (Kg)
            <input v-model="addMixRollForm.kg" type="number" min="0" step="0.1" />
          </label>
        </div>
        <div class="gpe-dialog-actions">
          <button type="button" class="gpe-btn" :disabled="addMixRollBusy" @click="closeAddMixRollDialog">Cancel</button>
          <button
            type="button"
            class="gpe-btn primary"
            :disabled="addMixRollBusy || !canSubmitAddMixRoll"
            @click="submitAddMixRoll"
          >
            {{ addMixRollBusy ? "Creating…" : "Create" }}
          </button>
        </div>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { openProductionPlanPrintPreview } from "./pp_print_utils.js";
import {
  openGsmWastageDialog,
  openGsmRecycleDialog,
} from "./gsm_wastage_recycle_dialog.js";
import { openGsmMixingSheetDialog } from "./gsm_mixing_sheet_dialog.js";
import { openGsmBreakdownDialog } from "./gsm_breakdown_dialog.js";
import { openGsmLotSampleDialog } from "./gsm_lot_sample_dialog.js";
import { openGsmShiftConsumablesDialog } from "./gsm_shift_consumables_dialog.js";
import {
  fetchGsmMixRollCandidates,
  activateGsmMixRollForSession,
  loadGsmMixRollSprRolls,
  addGsmMixRollLine,
  saveGsmMixRollLine,
  submitGsmMixRollSpr,
  mixRollItemOptions,
  mixRollWidthOptions,
  nextMixRollCombinationSlot,
  mixRollMaxRows,
  mixRollShaftCount,
  mixGridRowsForSpr,
  mixProducedRollCount,
  mixProducedShaftCount,
  mapMixRollLineFromServer,
  buildMixRollSavePayload,
  recalcMixRollRow,
  normalizeSpiDiameterCbm,
  fetchGsmMixRollFormOptions,
  upsertGsmMixRollFromEntry,
  MIX_QUALITY_OPTIONS,
  MIX_CL_TYPE_OPTIONS,
  MIX_COLOR_OPTIONS,
  mixColorBadgeStyle,
  suggestMixClType,
  mixShaftHint,
} from "./gsm_mix_roll.js";
import {
  gsmOpenBundlePackaging,
  gsmOpenManualJob,
  gsmOpenRmBatches,
  gsmOpenTrailOrder,
  gsmOpenQualityCheck,
  gsmBackfillShaftNumbers,
  gsmPrintRollLabel,
  gsmPrintQcLabel,
  gsmPrintBundleLabel,
  gsmToggleBundleSeOnSubmit,
  openSprForm,
} from "./spr_gsm_tools.js";
import {
  sprCalcCbmFromDiameter,
  sprCalcNetFromGross,
  sprCalcProducedGsm,
  sprComputePlannedQtyKg,
  sprFlt,
  sprFormatKg,
  sprWholeMtrs,
  sprGsmBandClass,
  sprGrossWeightDisplay,
  sprNormalizeGrossWeightInput,
  sprRecalcRollRow,
  sprSanitizeGrossWeightTyping,
  sprCoreBaseWeightKgs,
  sprShaftNoForRollIndex,
} from "./spr_roll_entry_utils.js";

import {
  applyBoardAccessUnitScope,
  isBoardActionFrozen,
  boardActionFrozenStyle,
} from "./board_access_ui.js";

const props = defineProps({
  boardSlug: { type: String, default: "gsm-production-entry" },
  processScope: { type: String, default: "" },
  pageTitle: { type: String, default: "GSM Production Entry" },
  showRollInputs: { type: Boolean, default: false },
});

const BOARD_SLUG = computed(() => String(props.boardSlug || "gsm-production-entry").trim() || "gsm-production-entry");
const GSM_BOARD_SLUG = BOARD_SLUG;
const STORAGE_KEY = computed(
  () => `gsm_production_entry_draft_v3_${BOARD_SLUG.value}_${frappe.session.user || "guest"}`
);
const FABRIC_UNITS = ["Unit 1", "Unit 2", "Unit 3", "Unit 4"];
const LAMINATION_UNIT = "TNSPL - LAMINATION UNIT";
const SLITTING_UNITS = ["JVE - SLITTING MACHINE", "VTP - SLITTING MACHINE"];
const REWINDING_UNITS = [
  "JSB - L5 REWINDING MACHINE",
  "JSB - L4 REWINDING MACHINE",
  "TSNPL - L3 REWINDING MACHINE",
];
const SHEET_CUTTING_UNITS = ["JVE - SHEET CUTTING MACHINE"];
const FLEXO_PRINTING_UNITS = [
  "TT - PRINTING MACHINE 4 COLOUR 1200MM",
  "JVE - PRINTING MACHINE 4 COLOUR 1600MM",
  "JVE - PRINTING MACHINE 2 COLOUR 1600MM",
];
const BOPP_PRINTING_UNITS = ["VR - 1200MM BOPP PRINTING MACHINE"];
const BAG_MAKING_UNITS = [
  "VTP-L1 LEADER OYANG MACHINE",
  "VTP-L2 LEADER ZX MACHINE",
  "VTP-L4 SCREEN PRINTING MACHINE",
];
/** Units offered on GSM (fabric only). Process pages use Workstation lists below. */
const GSM_ENTRY_UNITS = [...FABRIC_UNITS];
const showRollInputsBtn = computed(() => !!props.showRollInputs);
const pageHeading = computed(() => String(props.pageTitle || "GSM Production Entry").trim());

const processScopeKey = computed(() => String(props.processScope || "").trim());
const isLaminationEntryPage = computed(() => {
  return processScopeKey.value === "lamination_only" || BOARD_SLUG.value === "lamination-production-entry";
});

/** Unit dropdown pool for this production-entry page (Workstation names). */
const pageEntryUnits = computed(() => {
  const scope = processScopeKey.value;
  const slug = BOARD_SLUG.value;
  if (scope === "lamination_only" || slug === "lamination-production-entry") {
    return [LAMINATION_UNIT];
  }
  if (scope === "slitting_only" || slug === "slitting-production-entry") {
    return [...SLITTING_UNITS];
  }
  if (scope === "rewinding_only" || slug === "rewinding-production-entry") {
    return [...REWINDING_UNITS];
  }
  if (scope === "sheet_cutting_only" || slug === "sheet-cutting-production-entry") {
    return [...SHEET_CUTTING_UNITS];
  }
  if (scope === "printing_only" || slug === "flexo-printing-production-entry") {
    return [...FLEXO_PRINTING_UNITS];
  }
  if (scope === "printed_bopp_pb_only" || slug === "bopp-printing-production-entry") {
    return [...BOPP_PRINTING_UNITS];
  }
  if (scope === "box_bag_only" || slug === "bag-making-production-entry") {
    return [...BAG_MAKING_UNITS];
  }
  return [...FABRIC_UNITS];
});

const viewScope = ref("daily");
const filterDate = ref(frappe.datetime.get_today());
const filterWeek = ref("");
const filterMonth = ref(frappe.datetime.get_today().slice(0, 7));
const filterUnit = ref("");
const searchText = ref("");
const loadingOrders = ref(false);
const rawOrders = ref([]);
const merges = ref([]);
const quotaByLineId = ref({});

const gsmBoardAccess = ref({ unlimited: true, allowed_units: [], loaded: false, permitted: true, frozen_actions: {} });
const gsmUnitFilterState = ref({ pool: null, showUnitFilter: true, unitLocked: false });
const pageLoadError = ref(false);

const pageTab = ref("entry");
const runDate = ref(frappe.datetime.get_today());
const shift = ref("Day Shift");
const headerUnit = ref("");
const operator = ref("");
const supervisor = ref("");
const coordinator = ref("");
/** Warehouse Bay options for current session unit (additive Bay column). */
const bayOptions = ref([]);

const rollLines = ref([]);
const selectedEntries = ref([]);
const selectionLocked = ref(false);
const showCompletedOrders = ref(false);
const showConfirmDialog = ref(false);
const showSubmitConfirmDialog = ref(false);
const submitDialogPhase = ref("review");
const submitProgressMessage = ref("");
const submitSuccessResult = ref(null);
const submitErrorMessage = ref("");
let submitProgressTimer = null;
let submitProgressStart = 0;
const showShiftOpenDialog = ref(false);
const showAddRollWizard = ref(false);
const addRollWizardStep = ref(1);
const addRollWizardSkipJobStep = ref(false);
const addRollJobChoice = ref("");
const addRollWidthChoice = ref(null);
const MIX_WIZARD_KEY = "__gsm_mix_roll__";
let pendingAddRowResolve = null;
const lastAddRollJobKey = ref("");
const lastServerSyncAt = ref("");
const liveSyncLabel = ref("");
let gsmPollTimer = null;
let gsmSessionPingTimer = null;
let gsmRealtimeBound = false;
let gsmRefreshInFlight = false;
let gsmRefreshQueued = false;

let gsmRefreshDebounceTimer = null;
let gsmVisibilityBound = false;

/** Fallback poll only — live updates come from gsm_production_entry_updated. */
const GSM_LIVE_POLL_MS = 90 * 1000;
/** Logged-in check while a shift is open — cheap ping so Guest never hits Close Shift. */
const GSM_SESSION_PING_MS = 15 * 1000;

const deskSessionExpired = ref(false);

function gsmPageIsVisible() {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function gsmErrorText(err) {
  if (!err) {
    return "";
  }
  const xhr = err.responseJSON || err._server_messages || "";
  const parts = [
    err.message,
    err.exc,
    err.exception,
    err.status,
    err.statusText,
    err.exc_type,
    typeof err._server_messages === "string" ? err._server_messages : "",
    typeof xhr === "string" ? xhr : "",
  ];
  try {
    parts.push(JSON.stringify(err._server_messages || err.exc || err.responseJSON || ""));
  } catch (e) {
    /* ignore */
  }
  if (err.responseJSON && typeof err.responseJSON === "object") {
    try {
      parts.push(JSON.stringify(err.responseJSON));
    } catch (e) {
      /* ignore */
    }
  }
  return parts.filter(Boolean).join(" ").toLowerCase();
}

function isGsmSessionDeadError(err) {
  const user = frappe.session?.user;
  if (!user || user === "Guest") {
    return true;
  }
  const text = gsmErrorText(err);
  // Do NOT treat bare "not whitelisted" / "method not allowed" as session death —
  // those can be API/permission errors while Desk is still logged in (false Login expired).
  return (
    text.includes("login to access") ||
    text.includes("not permitted to access this resource") ||
    text.includes("please log in to continue") ||
    text.includes("session expired") ||
    text.includes("authenticationerror") ||
    text.includes("csrftoken") ||
    text.includes("csrf token") ||
    (text.includes("not whitelisted") && text.includes("login to access"))
  );
}

function markGsmSessionExpired() {
  deskSessionExpired.value = true;
  if (gsmPollTimer) {
    clearInterval(gsmPollTimer);
    gsmPollTimer = null;
  }
}

function reloadGsmForLogin() {
  const path = window.location.pathname + window.location.search + window.location.hash;
  window.location.href = "/login?redirect-to=" + encodeURIComponent(path);
}

async function ensureGsmDeskSession() {
  if (deskSessionExpired.value) {
    return false;
  }
  if (frappe.session?.user && frappe.session.user === "Guest") {
    markGsmSessionExpired();
    return false;
  }
  try {
    const r = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.ping_gsm_desk_session",
      type: "POST",
      silent: true,
      error: () => {},
    });
    const msg = r?.message || {};
    if (!cint(msg.ok) || !msg.user || msg.user === "Guest") {
      markGsmSessionExpired();
      return false;
    }
    return true;
  } catch (e) {
    if (isGsmSessionDeadError(e)) {
      markGsmSessionExpired();
      return false;
    }
    return true;
  }
}

function gsmSafeCall(opts) {
  const prevError = opts.error;
  return frappe
    .call({
      ...opts,
      type: opts.type || "POST",
      silent: true,
      error: (r) => {
        if (isGsmSessionDeadError(r)) {
          markGsmSessionExpired();
          return;
        }
        if (typeof prevError === "function") {
          prevError(r);
          return;
        }
        if (typeof frappe.request?.report_error === "function") {
          frappe.request.report_error(r, opts);
        }
      },
    })
    .then((r) => {
      if (deskSessionExpired.value) {
        throw new Error("session expired");
      }
      return r;
    });
}

let _gsmOrigFrappeCall = null;
function installGsmSessionCallGuard() {
  if (_gsmOrigFrappeCall || typeof frappe === "undefined" || typeof frappe.call !== "function") {
    return;
  }
  _gsmOrigFrappeCall = frappe.call.bind(frappe);
  frappe.call = function (opts, ...rest) {
    if (!opts || typeof opts !== "object") {
      return _gsmOrigFrappeCall(opts, ...rest);
    }
    const prevError = opts.error;
    const alreadySilent = !!opts.silent;
    return _gsmOrigFrappeCall(
      {
        ...opts,
        error: (r) => {
          if (isGsmSessionDeadError(r)) {
            markGsmSessionExpired();
            return;
          }
          if (typeof prevError === "function") {
            prevError(r);
            return;
          }
          if (!alreadySilent && typeof frappe.request?.report_error === "function") {
            frappe.request.report_error(r, opts);
          }
        },
      },
      ...rest
    );
  };
}

function teardownGsmSessionCallGuard() {
  if (_gsmOrigFrappeCall) {
    frappe.call = _gsmOrigFrappeCall;
    _gsmOrigFrappeCall = null;
  }
}

function onGsmWindowFocus() {
  if (!gsmPageIsVisible() || deskSessionExpired.value) {
    return;
  }
  ensureGsmDeskSession();
}

function setupGsmSessionWatch() {
  if (gsmSessionPingTimer) {
    clearInterval(gsmSessionPingTimer);
  }
  installGsmSessionCallGuard();
  gsmSessionPingTimer = setInterval(() => {
    if (!gsmPageIsVisible() || deskSessionExpired.value) {
      return;
    }
    if (!shiftOpened.value) {
      return;
    }
    ensureGsmDeskSession();
  }, GSM_SESSION_PING_MS);
  if (typeof window !== "undefined") {
    window.addEventListener("focus", onGsmWindowFocus);
  }
}

function teardownGsmSessionWatch() {
  if (gsmSessionPingTimer) {
    clearInterval(gsmSessionPingTimer);
    gsmSessionPingTimer = null;
  }
  if (typeof window !== "undefined") {
    window.removeEventListener("focus", onGsmWindowFocus);
  }
  teardownGsmSessionCallGuard();
}

function sessionSprRow(ppId) {
  return sessionSprs.value[ppId] || null;
}

function sessionSprIsSubmitted(ppId) {
  const row = sessionSprRow(ppId);
  return !!(row && (row.submitted || cint(row.docstatus) === 1));
}

async function gsmSprHeaders(sprNames) {
  const names = [...new Set((sprNames || []).map((n) => _cstr(n)).filter(Boolean))];
  if (!names.length) {
    return {};
  }
  const res = await frappe.call({
    method: "production_entry.production_planning.unified_production_entry_api.get_gsm_spr_headers",
    type: "POST",
    args: { spr_names: JSON.stringify(names) },
  });
  const map = {};
  for (const row of res.message?.sprs || []) {
    if (row?.name) {
      map[row.name] = row;
    }
  }
  return map;
}

function draftSprNameForPp(ppId) {
  const row = sessionSprRow(ppId);
  if (!row?.spr_name || sessionSprIsSubmitted(ppId)) {
    return "";
  }
  return row.spr_name;
}

function ppNeedsNewSpr(ppId) {
  if (!ppId) {
    return false;
  }
  if (forceNewSprByPp.value[ppId]) {
    return true;
  }
  return !draftSprNameForPp(ppId);
}

function rollBatchSuffix(batchNo) {
  const parts = String(batchNo || "").split("/");
  if (parts.length < 2) {
    return 0;
  }
  const n = parseInt(parts[parts.length - 1], 10);
  return Number.isFinite(n) ? n : 0;
}

function lifoSortKey(row) {
  // Mix and fabric share one shift batch series. Newest row (highest batch
  // suffix or creation_seq) stays at the top — last entry first.
  const batchSeq = rollBatchSuffix(row?.batch_no);
  const seq = cint(row?.creation_seq);
  return Math.max(batchSeq, seq);
}

/** LIFO: newest roll first (last entry on top). */
function sortRollLinesLifo(rows) {
  return [...rows].sort((a, b) => {
    const keyA = lifoSortKey(a);
    const keyB = lifoSortKey(b);
    if (keyA !== keyB) {
      return keyB - keyA;
    }
    return cint(b?.creation_seq) - cint(a?.creation_seq);
  });
}

function rollDisplayIndex(row, idx, total) {
  const batchSeq = rollBatchSuffix(row?.batch_no);
  if (batchSeq > 0) {
    return batchSeq;
  }
  const seq = cint(row?.creation_seq);
  if (seq > 0) {
    return seq;
  }
  return Math.max(1, (total || 0) - idx);
}

function syncCreationSeqFromGrid() {
  let maxSeq = cint(creationSeq.value);
  for (const row of rollLines.value) {
    maxSeq = Math.max(maxSeq, cint(row?.creation_seq), rollBatchSuffix(row?.batch_no));
  }
  creationSeq.value = maxSeq;
}

function nextCreationSeq() {
  syncCreationSeqFromGrid();
  creationSeq.value += 1;
  return creationSeq.value;
}

function currentShaftNoForJob(job) {
  if (!job) {
    return 1;
  }
  const rollIndex = gridRollCountForJob(job.pp_id, job.job_id);
  return sprShaftNoForRollIndex(
    rollIndex,
    Math.max(1, cint(job.max_shafts)),
    Math.max(1, cint(job.rolls_per_shaft) || 1),
    1
  );
}

function gridRollCountForJob(ppId, jobId) {
  return gridQuotaRollCountForJob(ppId, jobId);
}

function jobBoardJobForRollRow(row) {
  if (!row?.pp_id) {
    return null;
  }
  const jid = String(row.job_id || row.job || "");
  return (
    jobBoardJobs.value.find(
      (j) => j.pp_id === row.pp_id && String(j.job_id) === jid
    ) || null
  );
}

function rollIndexForJobRow(row) {
  const ppId = row?.pp_id;
  const jid = String(row.job_id || row.job || "");
  const peers = rollLines.value
    .filter(
      (r) =>
        !r.is_bundle_row &&
        !r.is_wasted &&
        r.pp_id === ppId &&
        String(r.job_id || r.job || "") === jid
    )
    .sort((a, b) => {
      const seqA = cint(a.creation_seq);
      const seqB = cint(b.creation_seq);
      if (seqA !== seqB) {
        return seqA - seqB;
      }
      return String(a._id || "").localeCompare(String(b._id || ""));
    });
  const idx = peers.findIndex((r) => r._id === row._id);
  return idx >= 0 ? idx : Math.max(0, peers.length - 1);
}

function resolveRowShaftNo(row) {
  let shaft = cint(row?.custom_no_of_shaft || row?.no_of_shaft || 0);
  if (shaft > 0) {
    return shaft;
  }
  const job = jobBoardJobForRollRow(row);
  if (!job) {
    return 0;
  }
  return sprShaftNoForRollIndex(
    rollIndexForJobRow(row),
    Math.max(1, cint(job.max_shafts)),
    Math.max(1, cint(job.rolls_per_shaft) || 1),
    1
  );
}

function onGsmProductionEntryUpdated(data) {
  if (!data || !shiftOpened.value || !gsmPageIsVisible()) {
    return;
  }
  if (mixRollBusy.value) {
    return;
  }
  const u = data.unit || "";
  const rd = String(data.run_date || "").slice(0, 10);
  const sh = data.shift || "";
  if (u && headerUnit.value && !sameGsmUnit(u, headerUnit.value)) {
    return;
  }
  if (rd && runDate.value && rd !== runDate.value) {
    return;
  }
  if (sh && shift.value && sh !== shift.value) {
    return;
  }
  if (gsmRefreshDebounceTimer) {
    clearTimeout(gsmRefreshDebounceTimer);
  }
  gsmRefreshDebounceTimer = setTimeout(() => {
    gsmRefreshDebounceTimer = null;
    refreshSessionFromServer({ quiet: true, merge: true });
  }, 1200);
}

function setupGsmLiveSync() {
  if (gsmRealtimeBound || !frappe.realtime?.on) {
    return;
  }
  frappe.realtime.on("gsm_production_entry_updated", onGsmProductionEntryUpdated);
  gsmRealtimeBound = true;
  if (gsmPollTimer) {
    clearInterval(gsmPollTimer);
  }
  gsmPollTimer = setInterval(() => {
    if (deskSessionExpired.value) {
      return;
    }
    if (shiftOpened.value && gsmPageIsVisible() && !gsmRefreshInFlight && !mixRollBusy.value) {
      refreshSessionFromServer({ quiet: true, merge: true });
    }
  }, GSM_LIVE_POLL_MS);
  if (typeof document !== "undefined" && !gsmVisibilityBound) {
    document.addEventListener("visibilitychange", onGsmVisibilityChange);
    gsmVisibilityBound = true;
  }
}

function onGsmVisibilityChange() {
  if (!gsmPageIsVisible()) {
    if (gsmRefreshDebounceTimer) {
      clearTimeout(gsmRefreshDebounceTimer);
      gsmRefreshDebounceTimer = null;
    }
    return;
  }
  ensureGsmDeskSession().then((ok) => {
    if (ok && shiftOpened.value && !gsmRefreshInFlight) {
      refreshSessionFromServer({ quiet: true, merge: true });
    }
  });
}

function teardownGsmLiveSync() {
  if (gsmRealtimeBound && frappe.realtime?.off) {
    frappe.realtime.off("gsm_production_entry_updated", onGsmProductionEntryUpdated);
  }
  gsmRealtimeBound = false;
  if (gsmPollTimer) {
    clearInterval(gsmPollTimer);
    gsmPollTimer = null;
  }
  if (gsmRefreshDebounceTimer) {
    clearTimeout(gsmRefreshDebounceTimer);
    gsmRefreshDebounceTimer = null;
  }
  if (gsmVisibilityBound && typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", onGsmVisibilityChange);
    gsmVisibilityBound = false;
  }
}

const jobBoardJobs = ref([]);
const sessionJobApiBaseline = ref({});

const seriesPrefix = ref("");
const maxRollSuffix = ref(0);
const reservedBatchNos = ref(new Set());
const addRollInProgress = ref(false);
const batchContextKey = ref("");

function currentBatchContextKey() {
  return `${runDate.value}|${shift.value}|${headerUnit.value}`;
}

function clearGsmMixState() {
  activeMixRoll.value = null;
  persistedActiveMixSpr.value = "";
  mixRollLines.value = [];
  rollLines.value = rollLines.value.filter((r) => !r.is_mix_roll_row);
}

function mixRowUnit(row) {
  return _cstr(row?.custom_unit || row?.unit || "");
}

function sameGsmUnit(a, b) {
  return _cstr(a).trim().toLowerCase() === _cstr(b).trim().toLowerCase();
}

function clearGsmUnitContextState() {
  selectedEntries.value = [];
  selectionLocked.value = false;
  shiftResumeBanner.value = "";
  bayOptions.value = [];
  if (!shiftOpened.value) {
    clearGsmMixState();
    rollLines.value = [];
    sessionSprs.value = {};
    sessionJobApiBaseline.value = {};
    forceNewSprSession.value = false;
    resetBatchSeriesCache();
  }
}

function gsmContextKeyChanged(prevKey, nextKey) {
  return Boolean(prevKey && nextKey && prevKey !== nextKey);
}

function resetBatchSeriesCache() {
  seriesPrefix.value = "";
  maxRollSuffix.value = 0;
  reservedBatchNos.value = new Set();
}

function resetBatchSeriesForShiftOpen() {
  maxRollSuffix.value = 0;
  reservedBatchNos.value = new Set();
  if (shiftBatchPrefix.value) {
    seriesPrefix.value = shiftBatchPrefix.value;
  }
}

function releaseBatchNo(batchNo) {
  const bn = _cstr(batchNo || "");
  if (!bn) {
    return;
  }
  const next = new Set(reservedBatchNos.value);
  next.delete(bn);
  reservedBatchNos.value = next;
}

function allExistingBatchNos() {
  const seen = new Set(reservedBatchNos.value);
  for (const r of rollLines.value) {
    const bn = _cstr(r.batch_no || "");
    if (bn) {
      seen.add(bn);
    }
  }
  return [...seen];
}

function isRowSavedToDb(row) {
  return !!row?.spr_item_name;
}

function unsavedGridBatchNos() {
  const seen = new Set();
  for (const r of rollLines.value) {
    if (isRowSavedToDb(r)) {
      continue;
    }
    const bn = _cstr(r.batch_no || "");
    if (bn) {
      seen.add(bn);
    }
  }
  for (const r of mixRollLines.value) {
    if (r.row_locked) {
      continue;
    }
    const bn = _cstr(r.batch_no || "");
    if (bn) {
      seen.add(bn);
    }
  }
  return [...seen];
}

function reserveBatchNo(batchNo, rollNo) {
  const bn = _cstr(batchNo || "");
  if (!bn) {
    return;
  }
  reservedBatchNos.value = new Set([...reservedBatchNos.value, bn]);
  const prefix = bn.split("/")[0];
  if (prefix) {
    seriesPrefix.value = prefix;
  }
  const rn = parseInt(rollNo, 10);
  if (!Number.isNaN(rn)) {
    maxRollSuffix.value = Math.max(maxRollSuffix.value, rn);
  } else {
    const suf = parseInt(bn.split("/").pop(), 10);
    if (!Number.isNaN(suf)) {
      maxRollSuffix.value = Math.max(maxRollSuffix.value, suf);
    }
  }
}

const sessionSprs = ref({});
const forceNewSprSession = ref(false);
const forceNewSprByPp = ref({});
const showToleranceDialog = ref(false);
const toleranceOrders = ref([]);
const toleranceForm = ref({});
const submitInProgress = ref(false);

const creationSeq = ref(0);

const summaryTab = ref("summary");
const saveStatus = ref("");
const toolsMenuOpen = ref(false);
const qualityMenuOpen = ref(false);

const shiftFilterDate = ref(frappe.datetime.get_today());
const shiftFilterShift = ref("Day Shift");
const shiftFilterUnit = ref("");
const shiftEntries = ref([]);
const shiftEntriesView = ref("spr");
const shiftConsolidated = ref(null);
const prevShiftConsolidated = ref(null);
const prevShiftLoading = ref(false);
const mixRollCandidates = ref([]);
const mixRollLoading = ref(false);
const mixRollBusy = ref(false);
const showAddMixRollDialog = ref(false);
const addMixRollBusy = ref(false);
const addMixRollMaxShaft = ref(0);
const addMixRollExtraColors = ref([]);
const addMixRollForm = ref({
  unit: "",
  color1: "",
  color2: "",
  mixName: "",
  quality: "Virgin Mix",
  clType: "Color Mix",
  gsm: "",
  shaft: "",
  noOfShaft: 1,
  kg: 0,
});
const activeMixRoll = ref(null);
const mixRollLines = ref([]);
const dismissedMixSprs = ref([]);
const persistedActiveMixSpr = ref("");
const sessionHeaderCollapsed = ref(false);
const shiftLoading = ref(false);
const selectedShiftEntry = ref(null);
const summaryShiftDate = ref(frappe.datetime.get_today());
const summaryShiftShift = ref("Day Shift");
const summaryShiftUnit = ref("");
const summaryShiftSummary = ref(null);
const summaryShiftLoading = ref(false);
const coreWidthOptions = ref([]);

const shiftSession = ref(null);
const shiftSessionReady = ref(false);
const shiftBatchPrefix = ref("");
const shiftPreviewBatch = ref("");
const shiftStatusByShift = ref({});
const shiftOpeningBusy = ref(false);
const shiftClosingBusy = ref(false);
const shiftResumeBanner = ref("");
const shiftReopenRequired = ref(false);
const shiftReopenPreviousSession = ref("");
const shiftReopenClosedBatch = ref("");
const shiftBatchReuseNotice = ref("");
const shiftReopenReason = ref("");
const shiftReopenRemarks = ref("");
const gsmReopenReasons = [
  "Accidental submit",
  "Missed rolls / incomplete entry",
  "Wrong data entered",
  "Other",
];
const showShiftReminder = ref(false);
let shiftReminderTimer = null;
let shiftReminderInterval = null;
let shiftReminderDismissedAt = 0;

const showShaftDetailsDialog = ref(false);
const shaftDetailsLoading = ref(false);
const shaftDetailsBlocks = ref([]);

let autosaveTimer = null;
let jobSelectionSaveTimer = null;

function entryKey(plannedDate, lineId) {
  return `${plannedDate}::${lineId}`;
}

function entryKeyJob(ppId, jobId) {
  return `${ppId}::${jobId}`;
}

function sessionPpIds() {
  const ids = new Set();
  for (const entry of selectedEntries.value) {
    if (entry.ppId) {
      ids.add(entry.ppId);
    }
  }
  for (const row of rollLines.value) {
    if (row.pp_id) {
      ids.add(row.pp_id);
    }
  }
  for (const pp of Object.keys(sessionSprs.value || {})) {
    if (pp) {
      ids.add(pp);
    }
  }
  return ids;
}

function sessionPlannedDates() {
  const dates = new Set();
  for (const entry of selectedEntries.value) {
    const pd = String(entry.plannedDate || "").slice(0, 10);
    if (pd) {
      dates.add(pd);
    }
  }
  for (const row of rollLines.value) {
    const pd = String(row.plannedDate || row.planned_date || "").slice(0, 10);
    if (pd) {
      dates.add(pd);
    }
  }
  if (filterDate.value) {
    dates.add(filterDate.value);
  }
  return [...dates];
}

function normalizeChartRow(d) {
  return {
    ...d,
    plannedDate: d.plannedDate || d.planned_date || "",
    partyCode: d.partyCode || d.party_code || "",
    customer_name: d.customer_name || d.party_name || d.customer || "",
    itemName: d.itemName || d.item_name || d.name,
    width_inch: sprFlt(d.width_inch || d.width),
  };
}

function chartOrderKey(row) {
  return `${row.pp_id}::${row.itemName || row.name}::${String(row.plannedDate || row.planned_date || "").slice(0, 10)}`;
}

function mergeChartOrders(base, extra) {
  const seen = new Set((base || []).map(chartOrderKey));
  const out = [...(base || [])];
  for (const d of extra || []) {
    const row = normalizeChartRow(d);
    const key = chartOrderKey(row);
    if (!seen.has(key)) {
      seen.add(key);
      out.push(row);
    }
  }
  return out;
}

function resolveOrderCodeForPp(ppId) {
  const spr = sessionSprs.value?.[ppId];
  if (spr?.order_code) {
    return spr.order_code;
  }
  for (const entry of selectedEntries.value) {
    if (entry.ppId === ppId && entry.orderCode && entry.orderCode !== ppId) {
      return entry.orderCode;
    }
  }
  const row =
    filteredPpSubmittedRows.value.find((r) => r.pp_id === ppId) ||
    ppSubmittedRows.value.find((r) => r.pp_id === ppId);
  const boardJob = jobBoardJobs.value.find((j) => j.pp_id === ppId);
  return boardJob?.order_code || row?.partyCode || row?.party_code || "";
}

function orderMetaForPp(ppId) {
  const row =
    filteredPpSubmittedRows.value.find((r) => r.pp_id === ppId) ||
    ppSubmittedRows.value.find((r) => r.pp_id === ppId);
  const boardJob = jobBoardJobs.value.find((j) => j.pp_id === ppId);
  const orderCode = resolveOrderCodeForPp(ppId) || boardJob?.order_code || row?.partyCode || row?.party_code || ppId;
  return {
    orderCode,
    partyName: boardJob?.party_name || row?.customer_name || row?.customer || "",
    // Prefer planning/color-chart row for order-level; board job is per-job fallback.
    quality: row?.quality || boardJob?.quality || "",
    color: row?.color || row?.fabric_colour || boardJob?.color || "",
    gsm: cint(row?.gsm) || cint(row?.fabric_gsm) || cint(row?.lam_gsm) || cint(boardJob?.gsm) || 0,
    planningLineId: row?.itemName || row?.name || "",
    width_inch: sprFlt(row?.width_inch || row?.width || boardJob?.width_inch || 0),
    lamination_process: row?.lamination_process || boardJob?.lamination_process || "",
  };
}

function ppChartMetaForPp(ppId) {
  const rows = filteredPpSubmittedRows.value.filter((r) => r.pp_id === ppId);
  const row = rows[0];
  return {
    wo_terminal: !!(row?.wo_terminal === true || row?.wo_terminal === 1),
    spr_docstatus: Number(row?.spr_docstatus ?? 0),
    pp_docstatus: Number(row?.pp_docstatus ?? 1),
  };
}

function orderDayStatsForPp(ppId) {
  let rows = filteredPpSubmittedRows.value.filter((r) => r.pp_id === ppId);
  let dayTargetKg = rows.reduce((s, r) => s + sprFlt(r.qty), 0);
  if (dayTargetKg <= 0 && shiftOpened.value) {
    const sessionDates = new Set(sessionPlannedDates());
    rows = ppSubmittedRows.value.filter((r) => r.pp_id === ppId);
    if (sessionDates.size) {
      rows = rows.filter((r) => sessionDates.has(String(r.plannedDate || r.planned_date || "").slice(0, 10)));
    }
    dayTargetKg = rows.reduce((s, r) => s + sprFlt(r.qty), 0);
  }
  if (dayTargetKg <= 0) {
    dayTargetKg = jobBoardJobs.value
      .filter((j) => j.pp_id === ppId)
      .reduce((s, j) => s + sprFlt(j.job_target_kg), 0);
  }
  const achievedKg = rows.reduce(
    (s, r) => s + sprFlt(r.actual_production_weight_kgs ?? r.total_achieved_weight_kgs),
    0
  );
  return {
    dayTargetKg,
    dayRemKg: Math.max(0, dayTargetKg - achievedKg),
  };
}

function selectedSessionOrderPlanKg() {
  const ppIds = new Set();
  let total = 0;
  for (const entry of selectedEntries.value) {
    const ppId = entry.ppId;
    if (!ppId || ppIds.has(ppId)) {
      continue;
    }
    ppIds.add(ppId);
    total += orderDayStatsForPp(ppId).dayTargetKg;
  }
  return total;
}

function ensureJobApiBaseline(job) {
  const key = entryKeyJob(job.pp_id, job.job_id);
  if (sessionJobApiBaseline.value[key] == null) {
    sessionJobApiBaseline.value[key] = cint(job.job_rolls_produced);
  }
}

function gridQuotaRollRowsForJob(ppId, jobId) {
  const jid = String(jobId || "");
  return rollLines.value.filter(
    (row) =>
      !row.is_bundle_row &&
      !row.is_mix_roll_row &&
      !row.is_wasted &&
      row.pp_id === ppId &&
      String(row.job_id || row.job || "") === jid
  );
}

function allGridRollRowsForJob(ppId, jobId) {
  return gridQuotaRollRowsForJob(ppId, jobId);
}

function gridQuotaRollCountForJob(ppId, jobId) {
  return gridQuotaRollRowsForJob(ppId, jobId).length;
}

function gridQuotaWidthCountForJob(ppId, jobId, widthInch) {
  const target = sprFlt(widthInch);
  return gridQuotaRollRowsForJob(ppId, jobId).filter(
    (row) => Math.abs(sprFlt(row.width_inch) - target) < 0.05
  ).length;
}

function gridBundleRollCountForJob(ppId, jobId, widthInch = null) {
  const jid = String(jobId || "");
  const target = widthInch == null ? null : sprFlt(widthInch);
  return rollLines.value
    .filter((row) => {
      if (!row.is_bundle_row || row.pp_id !== ppId || String(row.job_id || row.job || "") !== jid) {
        return false;
      }
      if (target == null) {
        return true;
      }
      const w = sprFlt(row.segment_width || row.width_inch);
      return Math.abs(w - target) < 0.05;
    })
    .reduce((sum, row) => sum + Math.max(1, cint(row.pack_count || 0)), 0);
}

function effectiveJobRollCount(job) {
  if (job?.api_job_rolls_produced != null) {
    return cint(job.api_job_rolls_produced) + cint(job.local_pending_rolls || 0);
  }
  const savedOnServer = Math.max(
    cint(job.job_rolls_produced),
    gridBundleRollCountForJob(job.pp_id, job.job_id)
  );
  return savedOnServer + localPendingRollCountForJob(job.pp_id, job.job_id);
}

function effectiveWidthRollCount(job, widthInch) {
  const target = sprFlt(widthInch);
  const seg = (job.width_segments || []).find((s) => Math.abs(sprFlt(s.width_inch) - target) < 0.05);
  if (seg?.api_current != null) {
    return cint(seg.api_current) + localPendingWidthCountForJob(job.pp_id, job.job_id, widthInch);
  }
  const savedAtWidth = Math.max(
    cint(seg?.current || 0),
    gridBundleRollCountForJob(job.pp_id, job.job_id, widthInch)
  );
  return savedAtWidth + localPendingWidthCountForJob(job.pp_id, job.job_id, widthInch);
}

function canJobAddOneMoreRoll(job) {
  if (!job || job.wo_terminal) {
    return false;
  }
  const j = job.api_job_rolls_produced != null ? job : withLocalPendingQuota(job);
  if (j.roll_limit_reached) {
    return false;
  }
  const maxRolls = cint(j.max_rolls);
  if (maxRolls <= 0) {
    return true;
  }
  return cint(j.rem_rolls) > 0;
}

function canJobAddWidthRoll(job, widthInch) {
  if (!job || job.wo_terminal) {
    return false;
  }
  const j = job.api_job_rolls_produced != null ? job : withLocalPendingQuota(job);
  if (!canJobAddOneMoreRoll(j)) {
    return false;
  }
  const target = sprFlt(widthInch);
  const seg = (j.width_segments || []).find((s) => Math.abs(sprFlt(s.width_inch) - target) < 0.05);
  const max = cint(seg?.max || j.max_rolls);
  if (max <= 0) {
    return true;
  }
  return effectiveWidthRollCount(j, widthInch) < max;
}

function recordJobApiBaselines(jobs) {
  const next = { ...sessionJobApiBaseline.value };
  for (const job of jobs || []) {
    const key = entryKeyJob(job.pp_id, job.job_id);
    if (next[key] == null) {
      next[key] = cint(job.job_rolls_produced);
    }
  }
  sessionJobApiBaseline.value = next;
}

function localPendingRollRowsForJob(ppId, jobId) {
  const jid = String(jobId || "");
  return rollLines.value.filter(
    (row) =>
      !row.is_bundle_row &&
      row.pp_id === ppId &&
      String(row.job_id || row.job || "") === jid &&
      !row.spr_item_name &&
      !row.row_locked
  );
}

function localPendingRollCountForJob(ppId, jobId) {
  return localPendingRollRowsForJob(ppId, jobId).length;
}

function localPendingWidthCountForJob(ppId, jobId, widthInch) {
  const target = sprFlt(widthInch);
  return localPendingRollRowsForJob(ppId, jobId).filter(
    (row) => Math.abs(sprFlt(row.width_inch) - target) < 0.05
  ).length;
}

function withLocalPendingQuota(job) {
  const gridCount =
    gridQuotaRollCountForJob(job.pp_id, job.job_id) +
    gridBundleRollCountForJob(job.pp_id, job.job_id);
  const savedOnServer = Math.max(cint(job.job_rolls_produced), 0);
  const jobRolls = Math.max(savedOnServer, gridCount);
  const pending = Math.max(0, jobRolls - savedOnServer);
  const rollsPerShaft = Math.max(1, cint(job.rolls_per_shaft));
  const maxRollsCap = cint(job.max_rolls);
  const maxShaftsCap = cint(job.max_shafts);
  const jobShafts =
    maxShaftsCap > 0
      ? Math.min(maxShaftsCap, Math.floor(jobRolls / rollsPerShaft))
      : Math.floor(jobRolls / rollsPerShaft);
  // max_rolls <= 0 means unlimited (lamination / open jobs) — do not treat as full
  const remRolls = maxRollsCap <= 0 ? 9999 : Math.max(0, maxRollsCap - jobRolls);
  const remShafts = maxShaftsCap <= 0 ? 9999 : Math.max(0, maxShaftsCap - jobShafts);
  const currentShaftRolls = jobRolls % rollsPerShaft;
  const currentShaftRemainingRolls =
    remRolls > 0 && currentShaftRolls ? Math.max(0, rollsPerShaft - currentShaftRolls) : 0;
  const widthSegments = (job.width_segments || []).map((seg) => {
    const savedAtWidth = cint(seg.current);
    const gridAtWidth =
      gridQuotaWidthCountForJob(job.pp_id, job.job_id, seg.width_inch) +
      gridBundleRollCountForJob(job.pp_id, job.job_id, seg.width_inch);
    const current = Math.max(savedAtWidth, gridAtWidth);
    const max = cint(seg.max);
    const segOpen = max <= 0 || current < max;
    return {
      ...seg,
      api_current: savedAtWidth,
      current,
      can_add: segOpen && (maxRollsCap <= 0 || remRolls > 0) && !job.wo_terminal,
    };
  });
  // Roll limit gates Add Roll (grid rows + saved count). A job is only "Completed"
  // (quota_full) once its rolls are SUBMITTED to the full quota, or the WO is done.
  const rollLimitReached =
    maxRollsCap > 0 && (remRolls <= 0 || (maxShaftsCap > 0 && jobShafts >= maxShaftsCap));
  const submittedRolls = cint(job.submitted_rolls);
  const submittedComplete = maxRollsCap > 0 && submittedRolls >= maxRollsCap;
  const quotaFull = !!job.wo_terminal || submittedComplete;
  return {
    ...job,
    api_job_rolls_produced: savedOnServer,
    local_pending_rolls: pending,
    job_rolls_produced: jobRolls,
    job_shafts_produced: jobShafts,
    rem_rolls: remRolls,
    rem_shafts: remShafts,
    current_shaft_rolls: currentShaftRolls,
    current_shaft_remaining_rolls: currentShaftRemainingRolls,
    width_segments: widthSegments,
    roll_limit_reached: rollLimitReached,
    quota_full: quotaFull,
    can_add_roll: !rollLimitReached && (maxRollsCap <= 0 || jobRolls < maxRollsCap),
  };
}

function enrichJobCard(job) {
  job = withLocalPendingQuota(job);
  const meta = orderMetaForPp(job.pp_id);
  const chartMeta = ppChartMetaForPp(job.pp_id);
  const woTerminal = !!(job.wo_terminal || chartMeta.wo_terminal);
  const quotaFull = job.quota_full || woTerminal;
  const submittedComplete = cint(job.max_rolls) > 0 && cint(job.submitted_rolls) >= cint(job.max_rolls);
  const sessionDone = sessionSprIsSubmitted(job.pp_id) && submittedComplete;
  const selectable =
    !woTerminal &&
    (!sessionDone || (!selectionLocked.value && sessionSprIsSubmitted(job.pp_id)));
  let chip = "";
  let chipClass = "";
  let tooltip = "Select for production";
  if (woTerminal) {
    chip = "WO Closed";
    chipClass = "gpe-chip-closed";
    tooltip = "Work Orders closed on this PP";
  } else if (job.quota_full) {
    chip = "Completed";
    chipClass = "gpe-chip-done";
    tooltip = "Job shaft/roll quota met";
  } else if (sessionDone) {
    chip = "SPR Submitted";
    chipClass = "gpe-chip-submitted";
    tooltip = "Submitted for this session — select again to start a new SPR";
  } else if (chartMeta.spr_docstatus === 1) {
    chip = "SPR Submitted";
    chipClass = "gpe-chip-submitted";
    tooltip = "Submitted SPR — more production allowed while WO is open";
  } else if ((job.active_spr_names || []).length || draftSprNameForPp(job.pp_id)) {
    chip = "SPR Active";
    chipClass = "gpe-chip-draft";
    tooltip = "Draft SPR exists for this run date and shift";
  }
  return {
    ...job,
    orderCode: meta.orderCode,
    partyName: meta.partyName,
    quality: job.quality || meta.quality || "",
    color: job.color || meta.color || "",
    planningLineId: meta.planningLineId,
    selectable,
    chip,
    chipClass,
    tooltip,
  };
}

function snapshotFromJob(job) {
  const meta = orderMetaForPp(job.pp_id);
  const widthInch = sprFlt(job.width_inch || job.width || meta.width_inch || 0);
  const widthLabel =
    widthInch > 0
      ? String(widthInch)
      : job.combination_label || meta.combination_label || "";
  const gsm =
    cint(job.gsm) ||
    cint(job.fabric_gsm) ||
    cint(job.lam_gsm) ||
    cint(meta.gsm) ||
    0;
  return {
    key: entryKeyJob(job.pp_id, job.job_id),
    jobId: job.job_id,
    lineId: meta.planningLineId || job.planning_line_id || job.itemName || "",
    plannedDate: filterDate.value,
    ppId: job.pp_id,
    orderCode: meta.orderCode,
    partyName: meta.partyName,
    quality: job.quality || meta.quality || "",
    color: job.color || meta.color || "",
    gsm,
    combination_label: isLaminationMode.value
      ? widthLabel || job.combination_label || ""
      : job.combination_label || widthLabel,
    width_inch: widthInch || null,
    widthLabel,
    max_shafts: job.max_shafts,
    max_rolls: job.max_rolls,
    is_manual: !!job.is_manual,
    lamination_process: job.lamination_process || meta.lamination_process || "",
    dayTargetKg: sprFlt(job.job_target_kg) || orderDayStatsForPp(job.pp_id).dayTargetKg,
    dayRemKg: orderDayStatsForPp(job.pp_id).dayRemKg,
    sourceSnapshot: {
      pp_id: job.pp_id,
      gsm,
      meter_roll: job.meter_roll,
      net_weight: job.net_weight,
    },
  };
}

function buildLineFromJob(job) {
  const meta = orderMetaForPp(job.pp_id);
  const lineId = meta.planningLineId || `job-${job.job_id}`;
  const fakeItem = {
    pp_id: job.pp_id,
    gsm: job.gsm,
    quality: meta.quality,
    color: meta.color,
    fabric_colour: meta.color,
    partyCode: meta.orderCode,
    party_code: meta.orderCode,
    customer_name: meta.partyName,
    itemName: lineId,
    name: lineId,
    qty: job.job_target_kg,
    uom: "Kg",
    stock_uom: "Kg",
  };
  return buildLineFromItem(fakeItem);
}

function firstPtLineForPp(ppId, gsm, jobId) {
  let rows = ppSubmittedRows.value.filter((r) => r.pp_id === ppId);
  if (jobId != null && jobId !== "") {
    const boardJob = jobBoardJobs.value.find(
      (j) => j.pp_id === ppId && String(j.job_id) === String(jobId)
    );
    if (boardJob) {
      return buildLineFromJob(boardJob);
    }
    const entry = selectedEntries.value.find(
      (e) => e.ppId === ppId && String(e.jobId || e.job_id) === String(jobId)
    );
    if (entry?.lineId) {
      const live = lineById.value.get(entry.lineId);
      const liveGsm = live?.gsm ?? live?.source?.gsm;
      if (live && (!gsm || String(liveGsm) === String(gsm))) {
        return buildLineFromItem(live);
      }
    }
    if (entry?.gsm) {
      const hit = rows.find((r) => String(r.gsm) === String(entry.gsm));
      if (hit) {
        return buildLineFromItem(hit);
      }
    }
  }
  if (gsm) {
    const hit = rows.find((r) => String(r.gsm) === String(gsm));
    if (hit) {
      return buildLineFromItem(hit);
    }
  }
  if (rows[0]) {
    return buildLineFromItem(rows[0]);
  }
  const anyJob = jobBoardJobs.value.find((j) => j.pp_id === ppId);
  return anyJob ? buildLineFromJob(anyJob) : null;
}

function defaultAddRollJobKey(entries) {
  const list = entries || [];
  const last = lastAddRollJobKey.value;
  if (last) {
    const rawLast = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === last);
    if (rawLast && canJobAddOneMoreRoll(withLocalPendingQuota(rawLast))) {
      return last;
    }
  }
  for (const e of list) {
    const key = e.key || entryKeyJob(e.ppId, e.jobId || e.job_id);
    const rawJob = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === key);
    if (rawJob && canJobAddOneMoreRoll(withLocalPendingQuota(rawJob))) {
      return key;
    }
  }
  const first = list[0];
  return first ? first.key || entryKeyJob(first.ppId, first.jobId || first.job_id) : "";
}

function confirmLineProgress(entry) {
  if (!entry) {
    return "—";
  }
  if (isLaminationMode.value) {
    const stats = orderDayStatsForPp(entry.ppId);
    const tgt = sprFlt(entry.dayTargetKg || stats.dayTargetKg);
    const rem = sprFlt(entry.dayRemKg != null ? entry.dayRemKg : stats.dayRemKg);
    if (tgt > 0 || rem > 0) {
      return `${formatKg(rem)} Kg rem / ${formatKg(tgt)} Kg`;
    }
    return "—";
  }
  const job = jobBoardJobs.value.find(
    (j) => j.pp_id === entry.ppId && String(j.job_id) === String(entry.jobId || entry.job_id)
  );
  if (!job) {
    return "—";
  }
  return `${job.job_shafts_produced}/${job.max_shafts} shafts · ${job.job_rolls_produced}/${job.max_rolls} rolls`;
}

function confirmLineGsm(line) {
  if (!line) {
    return "—";
  }
  let g = cint(line.gsm) || cint(line.fabric_gsm) || cint(line.lam_gsm) || 0;
  if (g <= 0 && line.ppId) {
    g = cint(orderMetaForPp(line.ppId).gsm) || 0;
  }
  return g > 0 ? g : "—";
}

function syntheticLamJobFromEntry(entry) {
  const meta = orderMetaForPp(entry.ppId);
  const gsm = cint(entry.gsm) || cint(entry.fabric_gsm) || cint(meta.gsm) || 0;
  const widthInch = sprFlt(entry.width_inch || entry.width || meta.width_inch || 0);
  return {
    pp_id: entry.ppId,
    job_id: entry.jobId || entry.job_id || "1",
    gsm,
    fabric_gsm: cint(entry.fabric_gsm) || gsm,
    lam_gsm: cint(entry.lam_gsm) || 0,
    quality: entry.quality || meta.quality || "",
    color: entry.color || meta.color || "",
    width_inch: widthInch || null,
    meter_roll: 0,
    max_shafts: 0,
    max_rolls: 0,
    rem_rolls: 999,
    job_rolls_produced: 0,
    job_shafts_produced: 0,
    width_segments: [],
    is_manual: false,
    planning_line_id: entry.lineId || meta.planningLineId || "",
    itemName: entry.lineId || meta.planningLineId || "",
    lamination_process: entry.lamination_process || meta.lamination_process || "",
    job_target_kg: sprFlt(entry.dayTargetKg) || orderDayStatsForPp(entry.ppId).dayTargetKg,
    selectable: true,
    wo_terminal: false,
  };
}

function confirmLineWidth(line) {
  if (!line) {
    return "—";
  }
  const w = sprFlt(line.width_inch || line.width || 0);
  if (w > 0) {
    return `${w}`;
  }
  const label = _cstr(line.widthLabel || line.combination_label || "").trim();
  return label || "—";
}

function plural(n, one, many) {
  return Number(n) === 1 ? one : many;
}

function shiftBreakdownParts(job) {
  const byShift = job?.rolls_by_shift_today || {};
  const known = ["Day Shift", "Night Shift"];
  const parts = known.map((sh) => ({ shift: sh, count: cint(byShift[sh]) }));
  for (const [sh, cnt] of Object.entries(byShift)) {
    if (!known.includes(sh)) {
      parts.push({ shift: sh, count: cint(cnt) });
    }
  }
  return parts;
}

function jobRemainingText(job) {
  const remRolls = cint(job.rem_rolls);
  const remShafts = cint(job.rem_shafts);
  const rollsPerShaft = Math.max(1, cint(job.rolls_per_shaft));
  if (remRolls <= 0) {
    return "0 rolls";
  }
  const pieces = [];
  let remainingShafts = remShafts;
  const currentNeed = cint(job.current_shaft_remaining_rolls);
  if (currentNeed > 0) {
    const shaftNo = cint(job.job_shafts_produced) + 1;
    pieces.push(`Shaft ${shaftNo}: ${currentNeed} ${plural(currentNeed, "roll", "rolls")}`);
    remainingShafts = Math.max(0, remainingShafts - 1);
  }
  if (remainingShafts === 1) {
    const shaftNo = cint(job.max_shafts) - remainingShafts + 1;
    pieces.push(`Shaft ${shaftNo}: ${rollsPerShaft} ${plural(rollsPerShaft, "roll", "rolls")}`);
  } else if (remainingShafts > 1) {
    pieces.push(`${remainingShafts} full ${plural(remainingShafts, "shaft", "shafts")}`);
  }
  if (!pieces.length) {
    pieces.push(`${remShafts} ${plural(remShafts, "shaft", "shafts")} · ${remRolls} ${plural(remRolls, "roll", "rolls")}`);
  }
  return `${pieces.join(" · ")} (${remRolls} ${plural(remRolls, "roll", "rolls")} total)`;
}

function linePlannedDate(item) {
  return String(item?.plannedDate || item?.planned_date || filterDate.value || "").slice(0, 10);
}

function formatPlannedDate(d) {
  if (!d) {
    return "—";
  }
  const parts = String(d).slice(0, 10).split("-");
  if (parts.length === 3) {
    return `${parts[2]}-${parts[1]}-${parts[0]}`;
  }
  return d;
}

function snapshotFromLine(line) {
  const src = line.source || {};
  const plannedDate = line.plannedDate || linePlannedDate(src);
  return {
    key: entryKey(plannedDate, line.id),
    lineId: line.id,
    plannedDate,
    ppId: line.ppId || src.pp_id,
    orderCode: line.orderCode,
    partyName: line.partyName,
    quality: line.quality,
    color: line.color,
    gsm: line.gsm,
    width_inch: line.width_inch,
    widthLabel: line.widthLabel,
    dayTargetKg: line.dayTargetKg,
    sourceSnapshot: {
      pp_id: line.ppId || src.pp_id,
      item_code: src.itemCode || src.item_code,
      itemCode: src.itemCode || src.item_code,
      item_name: src.description || src.item_name,
      description: src.description || src.item_name,
      uom: src.uom || src.stock_uom,
      stock_uom: src.stock_uom,
      qty: src.qty,
      actual_production_weight_kgs: src.actual_production_weight_kgs,
      gsm: line.gsm,
    },
  };
}

function entryToRollLine(entry) {
  if (entry.jobId || entry.job_id) {
    const line = firstPtLineForPp(entry.ppId, entry.gsm, entry.jobId || entry.job_id);
    if (line) {
      return {
        ...line,
        ppId: entry.ppId,
        jobId: entry.jobId || entry.job_id,
        plannedDate: entry.plannedDate,
        source: line.source,
      };
    }
  }
  const live = lineById.value.get(entry.lineId);
  if (live) {
    const livePd = live.plannedDate || linePlannedDate(live.source || {});
    if (livePd === entry.plannedDate) {
      return { ...live, ppId: entry.ppId || live.ppId, plannedDate: entry.plannedDate, source: live.source };
    }
  }
  const src = entry.sourceSnapshot || {};
  return {
    id: entry.lineId,
    ppId: entry.ppId,
    orderCode: entry.orderCode,
    partyName: entry.partyName,
    quality: entry.quality,
    color: entry.color,
    gsm: entry.gsm,
    width_inch: entry.width_inch,
    widthLabel: entry.widthLabel,
    dayTargetKg: entry.dayTargetKg,
    plannedDate: entry.plannedDate,
    source: src,
    selectable: true,
  };
}

const selectedEntryKeys = computed(() => new Set(selectedEntries.value.map((e) => e.key)));

const entryLinesForSession = computed(() => selectedEntries.value.map((e) => entryToRollLine(e)));

function isEntrySelected(line) {
  const pd = line.plannedDate || linePlannedDate(line.source || {});
  return selectedEntryKeys.value.has(entryKey(pd, line.id));
}

const vClickOutside = {
  mounted(el, binding) {
    el._gpeClickOutside = (e) => {
      if (!el.contains(e.target)) {
        binding.value();
      }
    };
    document.addEventListener("click", el._gpeClickOutside);
  },
  unmounted(el) {
    document.removeEventListener("click", el._gpeClickOutside);
  },
};

function formatKg(v) {
  return sprFormatKg(v);
}

function formatMtrs(v) {
  const n = sprWholeMtrs(v);
  return n === "" ? "—" : String(n);
}

function coerceProducedLengthMtrs(row) {
  if (!row) {
    return;
  }
  const v = row.produced_length_mtrs;
  if (v === "" || v === null || v === undefined) {
    return;
  }
  row.produced_length_mtrs = sprWholeMtrs(v);
}

function formatCbm(v) {
  const n = sprFlt(v);
  return n > 0 ? n.toFixed(4) : "—";
}

function coreBaseWeightForRow(row) {
  return sprCoreBaseWeightKgs(row?.custom_core_width_mm, coreWidthOptions.value);
}

function coreBaseWeightDisplay(row) {
  const bw = coreBaseWeightForRow(row);
  return bw > 0 ? formatKg(bw) : "—";
}

function onPolybagInput(row) {
  if (row.row_locked) {
    return;
  }
  applyRollRowRecalc(row);
  scheduleAutosave();
}

function isDraftSpr(item) {
  return !!item?.spr_name && (item.spr_docstatus === 0 || item.spr_docstatus === "0");
}

function itemTargetGapKg(item) {
  const t = sprFlt(item?.qty);
  const a = sprFlt(item?.actual_production_weight_kgs ?? item?.total_achieved_weight_kgs);
  return t - a;
}

function itemRemainingKg(item) {
  if (!item) {
    return 0;
  }
  const pendingKg = sprFlt(item.pp_pending_qty ?? item.pending_qty ?? item.item_pending_qty);
  const gap = itemTargetGapKg(item);
  if (gap > 0.5) {
    return Math.max(pendingKg, gap);
  }
  return pendingKg;
}

function isFabricUnit(unit) {
  const u = normalizeGsmUnit(unit);
  if (!u || u.toUpperCase().includes("UNASSIGNED")) {
    return false;
  }
  return FABRIC_UNITS.includes(u);
}

function isLaminationUnit(unit) {
  const u = _cstr(unit).trim().toUpperCase();
  return u.includes("LAMINATION") || u === LAMINATION_UNIT.toUpperCase();
}

function isGsmEntryUnit(unit) {
  const n = normalizeGsmUnit(unit);
  if (!n) return false;
  const allowed = pageEntryUnits.value.map((u) => normalizeGsmUnit(u));
  return allowed.includes(n);
}

function normalizeGsmUnit(unit) {
  const u = _cstr(unit).trim();
  if (!u) return "";
  // Only collapse lamination aliases when this page is the lamination entry page
  if (isLaminationEntryPage.value && (u.toUpperCase().includes("LAMINATION") || u === "Lamination Unit")) {
    return LAMINATION_UNIT;
  }
  const m = u.match(/^unit\s*(\d+)$/i);
  if (m) {
    return `Unit ${m[1]}`;
  }
  return u;
}

function buildRollQuotaDisplay(lineId) {
  const q = quotaByLineId.value[lineId];
  if (!q || q.max_rolls <= 0) {
    return null;
  }
  const jobMax = cint(q.max_rolls);
  const current = cint(q.current_rolls);
  const shiftMax = Math.max(
    0,
    cint(q.shift_max_rolls) || Math.max(0, jobMax - cint(q.other_shifts_rolls))
  );
  const dayTotal = cint(q.day_rolls_total);
  const priorShifts = Array.isArray(q.prior_shifts) ? q.prior_shifts : [];
  const priorParts = priorShifts.map((ps) => `${ps.shift}: ${ps.rolls} done`);
  let tooltip = `${current} produced this shift · ${shiftMax} allowed this shift · ${dayTotal}/${jobMax} rolls today`;
  if (priorParts.length) {
    tooltip += ` · Earlier: ${priorParts.join(", ")}`;
  }
  return {
    current,
    shiftMax,
    jobMax,
    dayTotal,
    priorShifts,
    tooltip,
    isFull: q.can_add_roll === false || q.can_add_roll === 0,
  };
}

function cint(v) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : 0;
}

function quotaTooltipForLine(lineId) {
  const rq = buildRollQuotaDisplay(lineId);
  return rq?.tooltip || "";
}

function lineEligibility(item, lineId) {
  if (!item?.pp_id || Number(item.pp_docstatus) !== 1) {
    return { selectable: false, chip: "", chipClass: "", tooltip: "PP not submitted" };
  }
  if (isDraftSpr(item)) {
    return {
      selectable: true,
      chip: "Draft SPR",
      chipClass: "gpe-chip-draft",
      tooltip: `Draft SPR ${item.spr_name} — continue entry`,
    };
  }
  const quota = quotaByLineId.value[lineId];
  if (quota?.max_rolls > 0 && !quota.can_add_roll) {
    return {
      selectable: false,
      chip: "Quota full",
      chipClass: "gpe-chip-quota",
      tooltip: `${quotaTooltipForLine(lineId)} — use Manual Job`,
    };
  }
  if (item.wo_terminal) {
    return {
      selectable: false,
      chip: "WO Closed",
      chipClass: "gpe-chip-closed",
      tooltip:
        "All Work Orders on this PP are closed (Completed / Stopped / Cancelled). Frozen until a new WO or Draft SPR exists.",
    };
  }
  const rem = itemRemainingKg(item);
  if (rem <= 0.5) {
    return {
      selectable: false,
      chip: "Completed",
      chipClass: "gpe-chip-done",
      tooltip: "PP line target met (remaining ≤ 0.5 Kg)",
    };
  }
  if (Number(item.spr_docstatus) === 1 && !item.wo_terminal) {
    return {
      selectable: true,
      chip: "SPR Submitted",
      chipClass: "gpe-chip-submitted",
      tooltip: "Submitted SPR — more production allowed while WO is open",
    };
  }
  return { selectable: true, chip: "", chipClass: "", tooltip: "Select for today's planned production" };
}

function quotaLabelForLine(lineId) {
  const rq = buildRollQuotaDisplay(lineId);
  if (!rq) {
    return "";
  }
  return `Rolls ${rq.current}/${rq.shiftMax}`;
}

const ppSubmittedRows = computed(() =>
  (rawOrders.value || []).filter(
    (r) => r.pp_id && Number(r.pp_docstatus) === 1 && isGsmEntryUnit(r.unit)
  )
);

function ordersBrowseDate() {
  return filterDate.value;
}

function rowMatchesFilterDate(row) {
  let pd = String(row.plannedDate || row.planned_date || "").slice(0, 10);
  if (!pd && Number(row.pp_docstatus) === 1 && row.pp_id) {
    pd = String(
      row.ordered_date || row.custom_planned_date || ordersBrowseDate() || ""
    ).slice(0, 10);
  }
  if (!pd) {
    return false;
  }
  if (viewScope.value === "daily") {
    return pd === ordersBrowseDate();
  }
  const args = buildFetchArgs();
  if (args.start_date && args.end_date) {
    return pd >= args.start_date && pd <= args.end_date;
  }
  return true;
}

const filteredPpSubmittedRows = computed(() => {
  let rows = ppSubmittedRows.value;
  const unit = normalizeGsmUnit(
    shiftOpened.value ? headerUnit.value || filterUnit.value : filterUnit.value
  );
  if (unit) {
    rows = rows.filter((r) => normalizeGsmUnit(r.unit) === unit);
  }
  rows = rows.filter((r) => rowMatchesFilterDate(r));
  return rows;
});

const filteredPpIdSet = computed(
  () => new Set(filteredPpSubmittedRows.value.map((r) => r.pp_id).filter(Boolean))
);

const sidebarAllowedPpIds = computed(() => {
  const s = new Set(filteredPpIdSet.value);
  for (const job of jobBoardJobs.value) {
    if (job?.is_trial && job.pp_id) {
      s.add(job.pp_id);
    }
  }
  for (const [ppId, row] of Object.entries(sessionSprs.value || {})) {
    if (row?.is_trial || (ppId && row?.spr_name && ppId === row.spr_name)) {
      s.add(ppId);
    }
  }
  for (const entry of selectedEntries.value) {
    if (entry?.is_trial && entry.ppId) {
      s.add(entry.ppId);
    }
  }
  return s;
});

const mergedItemIds = computed(() => {
  const s = new Set();
  (merges.value || []).forEach((m) => {
    (m.merged_items || []).forEach((id) => s.add(id));
  });
  return s;
});

const mergeBadgeByItemId = computed(() => {
  const m = {};
  (merges.value || []).forEach((merge) => {
    const label = merge.merge_label || "Merged";
    (merge.merged_items || []).forEach((id) => {
      m[id] = label;
    });
  });
  return m;
});

function quotaContextArgs() {
  return {
    run_date: runDate.value || quotaPlannedDate(),
    shift: shift.value,
    unit: filterUnit.value || headerUnit.value || undefined,
  };
}

function quotaPlannedDate() {
  if (viewScope.value === "daily" && filterDate.value) {
    return filterDate.value;
  }
  const args = buildFetchArgs();
  return args.date || args.start_date || filterDate.value;
}

function buildLineFromItem(item) {
  const id = item.itemName || item.name;
  const w = sprFlt(item.width_inch || item.width);
  const elig = lineEligibility(item, id);
  const achieved = sprFlt(item.actual_production_weight_kgs ?? item.total_achieved_weight_kgs);
  const dayTarget = sprFlt(item.qty);
  return {
    id,
    source: item,
    gsm: item.gsm,
    quality: item.quality || "",
    color: item.color || item.fabric_colour || "",
    width_inch: w,
    widthLabel: w ? `${w}"` : "—",
    dayTargetKg: dayTarget,
    dayRemKg: Math.max(0, dayTarget - achieved),
    remainingKg: itemRemainingKg(item),
    mergeBadge: mergeBadgeByItemId.value[id] || "",
    plannedDate: linePlannedDate(item),
    orderCode: item.partyCode || item.party_code || "",
    partyName: item.customer_name || item.customer || "",
    ppId: item.pp_id,
    selectable: elig.selectable,
    chip: elig.chip,
    chipClass: elig.chipClass,
    tooltip: elig.tooltip,
    rollQuota: buildRollQuotaDisplay(id),
    quotaLabel: quotaLabelForLine(id),
    quotaTooltip: quotaTooltipForLine(id),
  };
}

const unitOptions = computed(() => {
  const allowed = pageEntryUnits.value;
  const allowedSet = new Set(allowed.map((u) => normalizeGsmUnit(u)));
  const pool = gsmUnitFilterState.value.pool;
  if (pool && pool.length) {
    const fromPool = pool.map((u) => normalizeGsmUnit(u)).filter((u) => allowedSet.has(u));
    const uniq = [...new Set(fromPool)];
    return uniq.length ? uniq : allowed;
  }
  // Always offer the page's Workstation list (do not hide units just because PP has no rows yet)
  return allowed;
});

const fabricUnitOptions = computed(() => unitOptions.value.filter((u) => isFabricUnit(u)));

const isLaminationMode = computed(() => {
  if (isLaminationEntryPage.value) return true;
  const scope = processScopeKey.value;
  if (scope) return false;
  return isLaminationUnit(headerUnit.value || filterUnit.value);
});

const freezeGsmUnit = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_unit"));
const freezeGsmDate = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_date"));
const freezeGsmShift = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_shift"));
const freezeGsmStartShift = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_start_shift"));
const freezeGsmMixingSheet = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_mixing_sheet"));
const freezeGsmAddRow = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_add_row"));
const freezeGsmRemoveRow = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_remove_row"));
const freezeGsmSubmit = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_submit"));
const freezeGsmTools = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_tools"));
const freezeGsmShaftDetails = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_shaft_details"));
const freezeGsmSummary = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_summary"));
const freezeGsmShiftEntries = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_shift_entries"));
const freezeGsmClearEntries = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_clear_entries"));
const freezeGsmPrevShift = computed(() => isBoardActionFrozen(gsmBoardAccess.value, "gsm_prev_shift"));

const jobOrderGroups = computed(() => {
  const map = new Map();
  const allowedPpIds = sidebarAllowedPpIds.value;

  // Lamination entry: ONLY FG order cards from the lamination board (104/107).
  // Do not show fabric shaft/job-board cards (process 100 combinations).
  if (isLaminationMode.value) {
    for (const row of ppSubmittedRows.value) {
      if (!row.pp_id || (allowedPpIds.size && !allowedPpIds.has(row.pp_id))) {
        continue;
      }
      const orderCode = row.order_code || row.party_code || row.pp_id;
      const key = `${orderCode}::${row.pp_id}`;
      const dayStats = orderDayStatsForPp(row.pp_id);
      const widthInch = sprFlt(row.width_inch || row.width || 0);
      const widthLabel =
        widthInch > 0 ? String(widthInch) : _cstr(row.combination || "").trim();
      const gsm =
        cint(row.gsm) || cint(row.fabric_gsm) || cint(row.lam_gsm) || 0;
      const lamProc = _cstr(row.lamination_process || "").trim();
      // Skip any non-lamination / fabric-only chart leftovers
      if (lamProc && lamProc !== "104" && lamProc !== "107") {
        continue;
      }
      map.set(key, {
        key,
        orderCode,
        partyName: row.customer || "",
        ppId: row.pp_id,
        quality: row.quality || "",
        color: row.color || "",
        laminationProcess: lamProc,
        isTrial: false,
        dayTargetKg: dayStats.dayTargetKg || sprFlt(row.qty) || sprFlt(row.target_kg),
        dayRemKg:
          dayStats.dayRemKg ||
          sprFlt(row.remaining_kg) ||
          Math.max(0, sprFlt(row.qty || row.target_kg) - sprFlt(row.actual_production_weight_kgs)),
        jobs: [
          enrichJobCard({
            pp_id: row.pp_id,
            job_id: "1",
            job_key: `${row.pp_id}::1`,
            order_code: orderCode,
            gsm,
            fabric_gsm: cint(row.fabric_gsm) || gsm,
            lam_gsm: cint(row.lam_gsm) || 0,
            quality: row.quality || "",
            color: row.color || "",
            combination_label: widthLabel,
            width_inch: widthInch || null,
            widthLabel,
            planning_line_id: row.itemName || row.name || "",
            itemName: row.itemName || row.name || "",
            lamination_process: lamProc,
            max_shafts: 0,
            max_rolls: 0,
            job_shafts_produced: 0,
            job_rolls_produced: 0,
            rem_shafts: 0,
            rem_rolls: 0,
            selectable: true,
            quota_full: false,
            job_target_kg:
              dayStats.dayTargetKg || sprFlt(row.qty) || sprFlt(row.target_kg),
            job_remaining_kg:
              dayStats.dayRemKg ||
              sprFlt(row.remaining_kg) ||
              Math.max(0, sprFlt(row.qty || row.target_kg) - sprFlt(row.actual_production_weight_kgs)),
            tooltip: "Lamination FG order — select to create SPR",
          }),
        ],
      });
    }
    return [...map.values()].sort((a, b) => a.orderCode.localeCompare(b.orderCode));
  }

  for (const job of jobBoardJobs.value) {
    if (!allowedPpIds.has(job.pp_id)) {
      continue;
    }
    const enriched = enrichJobCard(job);
    const key = `${enriched.orderCode}::${job.pp_id}`;
    if (!map.has(key)) {
      const dayStats = orderDayStatsForPp(job.pp_id);
      const orderMeta = orderMetaForPp(job.pp_id);
      map.set(key, {
        key,
        orderCode: enriched.orderCode,
        partyName: enriched.partyName,
        ppId: job.pp_id,
        quality: orderMeta.quality || "",
        color: orderMeta.color || "",
        laminationProcess: orderMeta.lamination_process || job.lamination_process || "",
        isTrial: !!job.is_trial,
        dayTargetKg: dayStats.dayTargetKg,
        dayRemKg: dayStats.dayRemKg,
        jobs: [],
      });
    }
    map.get(key).jobs.push(enriched);
  }

  return [...map.values()].sort((a, b) => a.orderCode.localeCompare(b.orderCode));
});

const displayJobOrderGroups = computed(() => filterJobGroups(jobOrderGroups.value));

const activeJobOrderGroups = computed(() =>
  jobOrderGroups.value
    .map((g) => ({ ...g, jobs: g.jobs.filter((j) => j.selectable) }))
    .filter((g) => g.jobs.length)
);

const completedJobOrderGroups = computed(() =>
  jobOrderGroups.value
    .map((g) => ({ ...g, jobs: g.jobs.filter((j) => !j.selectable) }))
    .filter((g) => g.jobs.length)
);

function filterJobGroups(groups) {
  const q = (searchText.value || "").trim().toLowerCase();
  if (!q) {
    return groups;
  }
  return groups
    .map((g) => ({
      ...g,
      jobs: g.jobs.filter(
        (j) =>
          g.orderCode.toLowerCase().includes(q) ||
          String(j.job_id).toLowerCase().includes(q) ||
          (j.combination_label || "").toLowerCase().includes(q) ||
          String(j.gsm).toLowerCase().includes(q)
      ),
    }))
    .filter((g) => g.jobs.length);
}

const filteredActiveJobGroups = computed(() => filterJobGroups(activeJobOrderGroups.value));
const filteredCompletedJobGroups = computed(() => filterJobGroups(completedJobOrderGroups.value));
const completedJobCount = computed(() =>
  completedJobOrderGroups.value.reduce((n, g) => n + g.jobs.length, 0)
);

const wizardJobChoices = computed(() => {
  const base = selectedEntries.value.map((entry) => {
    const jid = entry.jobId || entry.job_id;
    const boardRaw = jobBoardJobs.value.find((j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid));
    const board = boardRaw ? withLocalPendingQuota(boardRaw) : null;
    const maxed = board ? !canJobAddOneMoreRoll(board) : false;
    return { ...entry, board, maxed };
  });
  // Manual jobs (created via Tools) exist only on the SPR — surface them for the
  // PPs already in this session so the operator can record their production.
  const ppSet = new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean));
  const seen = new Set(base.map((e) => e.key || entryKeyJob(e.ppId, e.jobId || e.job_id)));
  for (const raw of jobBoardJobs.value) {
    if (!raw.is_manual || !ppSet.has(raw.pp_id)) {
      continue;
    }
    const key = entryKeyJob(raw.pp_id, raw.job_id);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    const board = withLocalPendingQuota(raw);
    base.push({
      key,
      ppId: raw.pp_id,
      jobId: raw.job_id,
      job_id: raw.job_id,
      orderCode: raw.order_code || "",
      gsm: raw.gsm,
      is_manual: true,
      board,
      maxed: !canJobAddOneMoreRoll(board),
    });
  }
  if (mixSprReady.value && activeMixRoll.value) {
    const mixExisting = mixRowsForActive();
    base.unshift({
      key: MIX_WIZARD_KEY,
      is_mix: true,
      orderCode: activeMixRoll.value.label || "Mix Roll",
      gsm: activeMixRoll.value.gsm,
      shaft: activeMixRoll.value.shaft || activeMixRoll.value.combination || "",
      maxed: mixExisting.length >= mixRollMaxRows(activeMixRoll.value),
    });
  }
  return base;
});

const wizardSelectedJob = computed(() => {
  const key = addRollJobChoice.value;
  if (!key || key === MIX_WIZARD_KEY) {
    return null;
  }
  const raw = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === key);
  return raw ? withLocalPendingQuota(raw) : null;
});

const wizardSelectedJobMaxed = computed(() => {
  const job = wizardSelectedJob.value;
  return job ? !canJobAddOneMoreRoll(job) : false;
});

const allSelectedJobsMaxed = computed(() => {
  if (!selectedEntries.value.length) {
    return false;
  }
  return selectedEntries.value.every((entry) => {
    const jid = entry.jobId || entry.job_id;
    const raw = jobBoardJobs.value.find((j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid));
    if (!raw) {
      return false;
    }
    return !canJobAddOneMoreRoll(withLocalPendingQuota(raw));
  });
});

const wizardWidthSegments = computed(() => {
  const key = addRollJobChoice.value;
  if (!key) {
    return [];
  }
  const rawJob = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === key);
  const job = rawJob ? withLocalPendingQuota(rawJob) : null;
  return job?.width_segments || [];
});

const orderGroups = computed(() => {
  const map = new Map();
  filteredPpSubmittedRows.value.forEach((item) => {
    const orderCode = item.partyCode || item.party_code || "";
    const key = `${orderCode}::${item.customer_name || item.customer || ""}`;
    if (!map.has(key)) {
      map.set(key, {
        key,
        orderCode,
        partyName: item.customer_name || item.customer || "",
        ppId: item.pp_id,
        lines: [],
      });
    }
    map.get(key).lines.push(buildLineFromItem(item));
  });
  return [...map.values()].sort((a, b) => a.orderCode.localeCompare(b.orderCode));
});

const activeOrderGroups = computed(() =>
  orderGroups.value
    .map((g) => ({ ...g, lines: g.lines.filter((l) => l.selectable) }))
    .filter((g) => g.lines.length)
);

const completedOrderGroups = computed(() =>
  orderGroups.value
    .map((g) => ({ ...g, lines: g.lines.filter((l) => !l.selectable) }))
    .filter((g) => g.lines.length)
);

const filteredActiveGroups = computed(() => filterGroups(activeOrderGroups.value));
const filteredCompletedGroups = computed(() => filterGroups(completedOrderGroups.value));
const completedLineCount = computed(() =>
  completedOrderGroups.value.reduce((n, g) => n + g.lines.length, 0)
);

function filterGroups(groups) {
  const q = (searchText.value || "").trim().toLowerCase();
  if (!q) {
    return groups;
  }
  return groups
    .map((g) => ({
      ...g,
      lines: g.lines.filter(
        (l) =>
          g.orderCode.toLowerCase().includes(q) ||
          (l.quality || "").toLowerCase().includes(q) ||
          (l.color || "").toLowerCase().includes(q) ||
          String(l.gsm).toLowerCase().includes(q)
      ),
    }))
    .filter((g) => g.lines.length);
}

const lineById = computed(() => {
  const m = new Map();
  orderGroups.value.forEach((g) => {
    g.lines.forEach((l) => m.set(l.id, { ...l, ppId: g.ppId }));
  });
  return m;
});

const selectedSummary = computed(() => {
  if (!selectedEntries.value.length) {
    return null;
  }
  const orderCount = new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean)).size;
  return {
    count: selectedEntries.value.length,
    orderCount,
    dayPlanned: selectedSessionOrderPlanKg(),
  };
});

const confirmLines = computed(() => selectedEntries.value);

const headerTags = computed(() => {
  const tags = [];
  selectedEntries.value.forEach((entry) => {
    const jid = entry.jobId || entry.job_id;
    tags.push(`${entry.orderCode} / Job ${jid} · ${entry.gsm} GSM`);
  });
  return tags.slice(0, 12);
});

const toolsPpOptions = computed(() => {
  const map = new Map();
  for (const entry of selectedEntries.value) {
    const ppId = entry.ppId;
    if (!ppId || !sessionSprs.value[ppId]?.spr_name) {
      continue;
    }
    if (!map.has(ppId)) {
      map.set(ppId, {
        ppId,
        orderCode: entry.orderCode,
        spr_name: sessionSprs.value[ppId].spr_name,
        lineIds: [],
      });
    }
    map.get(ppId).lineIds.push(entry.lineId);
  }
  return [...map.values()];
});

const toolsContext = computed(() => {
  const options = toolsPpOptions.value;
  if (!selectionLocked.value || !options.length) {
    return null;
  }
  if (options.length === 1) {
    const o = options[0];
    return { ppId: o.ppId, planningNames: o.lineIds, orderCode: o.orderCode };
  }
  return { multi: true, options };
});

const needsCreateSprs = computed(() => {
  const ppIds = [...new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean))];
  if (!ppIds.length) {
    return false;
  }
  return ppIds.some((ppId) => ppNeedsNewSpr(ppId));
});

const allSprsCreated = computed(() => !needsCreateSprs.value);

const mixSprReady = computed(() => !!(activeMixRoll.value?.spr_name));

const toolsEnabled = computed(() => !!toolsPpOptions.value.length && selectionLocked.value);
const toolsHint = computed(() => {
  if (!selectionLocked.value) {
    return "Confirm & lock lines first";
  }
  if (!toolsPpOptions.value.length) {
    return "Create SPRs first, then use Tools";
  }
  if (toolsPpOptions.value.length > 1) {
    return "Tools — pick which order/SPR to open";
  }
  return "SPR tools for this order";
});

const primaryGsmLabel = computed(() => {
  const gsms = new Set();
  rollLines.value.forEach((r) => {
    if (r.gsm) {
      gsms.add(r.gsm);
    }
  });
  if (gsms.size === 1) {
    return ` with ${[...gsms][0]} GSM`;
  }
  return "";
});

const boardDayTotalKg = computed(() =>
  filteredPpSubmittedRows.value.reduce((s, r) => s + sprFlt(r.qty), 0)
);

const metrics = computed(() => {
  let totalGross = 0;
  let totalNet = 0;
  rollLines.value.forEach((r) => {
    if (r.is_wasted) {
      return;
    }
    totalGross += sprNormalizeGrossWeightInput(r.gross_weight);
    totalNet += sprFlt(r.net_weight);
  });
  let dayPlanned = selectedSessionOrderPlanKg();
  rollLines.value.forEach((r) => {
    if (r.is_wasted) {
      return;
    }
    dayPlanned -= sprFlt(r.net_weight);
  });
  return { totalGross, totalNet, dayRemaining: Math.max(0, dayPlanned) };
});

const sessionRollCount = computed(() => {
  const grid = rollLines.value.filter((r) => !r.is_wasted && !r.is_bundle_row).length;
  if (grid > 0) {
    return grid;
  }
  if (selectedEntries.value.length) {
    let total = 0;
    const seen = new Set();
    for (const entry of selectedEntries.value) {
      const jid = entry.jobId || entry.job_id;
      const key = entryKeyJob(entry.ppId, jid);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      const raw = jobBoardJobs.value.find(
        (j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid)
      );
      if (raw) {
        total += withLocalPendingQuota(raw).job_rolls_produced;
      }
    }
    if (total > 0) {
      return total;
    }
  }
  return rollLines.value.reduce(
    (sum, r) => {
      if (r.is_wasted) {
        return sum;
      }
      return sum + (r.is_bundle_row ? Math.max(1, cint(r.pack_count || 0)) : 1);
    },
    0
  );
});

const linkedOrderSummary = computed(() => {
  const byOrder = new Map();
  const addReq = (orderCode, partyName, required) => {
    if (!byOrder.has(orderCode)) {
      byOrder.set(orderCode, { orderCode, partyName, required: 0, produced: 0, achieved: 0 });
    }
    byOrder.get(orderCode).required += required;
    if (partyName) {
      byOrder.get(orderCode).partyName = partyName;
    }
  };
  selectedEntries.value.forEach((entry) => {
    const src = entry.sourceSnapshot || {};
    addReq(entry.orderCode, entry.partyName, sprFlt(src.qty));
    byOrder.get(entry.orderCode).achieved += sprFlt(src.actual_production_weight_kgs);
  });
  rollLines.value.forEach((r) => {
    if (r.is_wasted) {
      return;
    }
    const k = r.party_code;
    if (!byOrder.has(k)) {
      byOrder.set(k, { orderCode: k, partyName: "", required: 0, produced: 0, achieved: 0 });
    }
    byOrder.get(k).produced += sprFlt(r.net_weight);
  });
  return [...byOrder.values()].map((o) => ({
    ...o,
    remaining: Math.max(0, o.required - o.achieved - o.produced),
    produced: o.achieved + o.produced,
  }));
});

const linkedTotals = computed(() => {
  const rows = linkedOrderSummary.value;
  return rows.reduce(
    (a, r) => ({
      required: a.required + r.required,
      produced: a.produced + r.produced,
      remaining: a.remaining + r.remaining,
    }),
    { required: 0, produced: 0, remaining: 0 }
  );
});

const batchSummary = computed(() => {
  const m = new Map();
  rollLines.value.forEach((r) => {
    if (r.is_wasted) {
      return;
    }
    const bn = r.batch_no || "(pending)";
    if (!m.has(bn)) {
      m.set(bn, { batch_no: bn, rolls: 0, totalGross: 0, totalNet: 0 });
    }
    const b = m.get(bn);
    b.rolls += 1;
    b.totalGross += sprNormalizeGrossWeightInput(r.gross_weight);
    b.totalNet += sprFlt(r.net_weight);
  });
  return [...m.values()];
});

const gsmSummary = computed(() => {
  const m = new Map();
  selectedEntries.value.forEach((entry) => {
    const g = String(entry.gsm);
    if (!m.has(g)) {
      m.set(g, { gsm: g, required: 0, session: 0, achieved: 0 });
    }
    const src = entry.sourceSnapshot || {};
    m.get(g).required += sprFlt(src.qty);
    m.get(g).achieved += sprFlt(src.actual_production_weight_kgs);
  });
  rollLines.value.forEach((r) => {
    if (r.is_wasted) {
      return;
    }
    const g = String(r.gsm);
    if (!m.has(g)) {
      m.set(g, { gsm: g, required: 0, session: 0, achieved: 0 });
    }
    m.get(g).session += sprFlt(r.net_weight);
  });
  return [...m.values()].map((x) => {
    const produced = x.achieved + x.session;
    const remaining = Math.max(0, x.required - produced);
    const pct = x.required > 0 ? Math.min(100, (produced / x.required) * 100) : 0;
    return { gsm: x.gsm, required: x.required, produced, remaining, pct };
  });
});

const selectedLinesDetail = computed(() =>
  selectedEntries.value.map((entry) => {
    const sessionKg = rollLines.value
      .filter((r) => r.planning_table_row === entry.lineId)
      .reduce((s, r) => s + sprFlt(r.net_weight), 0);
    const src = entry.sourceSnapshot || {};
    return {
      id: entry.lineId,
      orderCode: entry.orderCode,
      partyName: entry.partyName,
      gsm: entry.gsm,
      width_inch: entry.width_inch,
      plannedDate: entry.plannedDate,
      requiredKg: sprFlt(src.qty),
      sessionKg,
      achievedKg: sprFlt(src.actual_production_weight_kgs),
    };
  })
);

const canAddRow = computed(() => {
  if (
    !shiftOpened.value ||
    !selectionLocked.value ||
    !selectedEntries.value.length ||
    !headerUnit.value ||
    !runDate.value ||
    !shift.value ||
    !selectedSessionSprList.value.length
  ) {
    return false;
  }
  // Lamination: no fabric shaft roll quota — Add Roll prompts for N lines on SPR
  if (isLaminationMode.value) {
    return true;
  }
  return selectedEntries.value.some((entry) => {
    const jid = entry.jobId || entry.job_id;
    const raw = jobBoardJobs.value.find((j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid));
    if (!raw) {
      return true;
    }
    return canJobAddOneMoreRoll(withLocalPendingQuota(raw));
  });
});

const addRollDisabledHint = computed(() => {
  if (mixSprReady.value) {
    return __("Add mix roll using order code {0}", [activeMixRoll.value?.label || ""]);
  }
  if (canAddRow.value) {
    return "";
  }
  if (!shiftOpened.value) {
    return __("Start the shift first");
  }
  if (!selectionLocked.value) {
    return __("Click Confirm selection to lock jobs before adding rows");
  }
  if (!selectedSessionSprList.value.length) {
    return __("Create SPRs first");
  }
  if (!selectedEntries.value.some((entry) => {
    const jid = entry.jobId || entry.job_id;
    const raw = jobBoardJobs.value.find((j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid));
    return raw ? canJobAddOneMoreRoll(withLocalPendingQuota(raw)) : true;
  })) {
    return __("All selected jobs are at roll limit");
  }
  return __("Cannot add row yet");
});

/** All SPRs created this shift — kept until Close Shift (not pruned on unlock). */
const shiftSessionSprList = computed(() =>
  Object.entries(sessionSprs.value || {})
    .filter(([ppId, v]) => ppId && v?.spr_name)
    .map(([pp_id, v]) => ({ pp_id, ...v }))
);

/** SPRs for currently selected production plans with draft SPR only. */
const selectedSessionSprList = computed(() => {
  const ppIds = new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean));
  if (!ppIds.size) {
    return [];
  }
  return [...ppIds]
    .filter((ppId) => draftSprNameForPp(ppId))
    .map((ppId) => ({
      pp_id: ppId,
      ...sessionSprs.value[ppId],
    }));
});

const sprCreatedForSession = computed(() => {
  if (activeMixRoll.value) return mixSprReady.value;
  return selectedSessionSprList.value.length > 0;
});

/** SPRs that will be submitted — at least one saved roll in the grid for that pp_id. */
const submitSprList = computed(() => {
  const ppWithRolls = new Set();
  for (const r of rollLines.value) {
    if (r.is_bundle_row || r.is_wasted) {
      continue;
    }
    if (r.pp_id && r.batch_no && isCompleteGsmRoll(r)) {
      ppWithRolls.add(r.pp_id);
    }
  }
  return shiftSessionSprList.value.filter((s) => ppWithRolls.has(s.pp_id));
});

const mixSubmitSprList = computed(() => {
  const bySpr = new Map();
  for (const row of submitConfirmRolls.value) {
    if (!row.is_mix_roll_row || !row.spr_name) {
      continue;
    }
    bySpr.set(row.spr_name, {
      pp_id: "",
      spr_name: row.spr_name,
      order_code: row.party_code || activeMixRoll.value?.label || "Mix Roll",
      label_type: "Mix Roll",
      is_mix_roll: 1,
    });
  }
  return [...bySpr.values()];
});

const allSubmitSprList = computed(() => [...submitSprList.value, ...mixSubmitSprList.value]);

/** Wastage / Recycle — all session SPRs (desk SPR is source of truth for patty/recycle rows). */
const wastageRecycleSprList = computed(() => {
  if (!shiftOpened.value) {
    return [];
  }
  return shiftSessionSprList.value.filter((s) => s && s.spr_name);
});

/** @deprecated alias — use shiftSessionSprList / selectedSessionSprList / submitSprList */
const sessionSprList = shiftSessionSprList;

function pruneSelectedEntriesToFilter() {
  const allowedPpIds = filteredPpIdSet.value;
  if (!selectedEntries.value.length) {
    return;
  }
  const browseDate = ordersBrowseDate();
  const next = selectedEntries.value.filter((e) => {
    if (e.is_trial || sessionSprs.value[e.ppId]?.is_trial || (e.ppId && sessionSprs.value[e.ppId]?.spr_name === e.ppId)) {
      return true;
    }
    if (!e.ppId || !allowedPpIds.has(e.ppId)) {
      return false;
    }
    if (viewScope.value === "daily") {
      const pd = String(e.plannedDate || browseDate).slice(0, 10);
      return pd === browseDate;
    }
    return true;
  });
  if (next.length === selectedEntries.value.length) {
    return;
  }
  selectedEntries.value = next;
  if (!next.length) {
    selectionLocked.value = false;
    if (!shiftOpened.value) {
      rollLines.value = [];
      sessionSprs.value = {};
    }
  }
  scheduleAutosave();
}

const shiftOpened = computed(() => {
  const s = shiftSession.value;
  return !!(s && s.status === "Open");
});

const shiftOpenedBy = computed(() => _cstr(shiftSession.value?.opened_by || ""));

const showShiftOpeningPanel = computed(
  () => pageTab.value === "entry" && !!headerUnit.value && shiftSessionReady.value && !shiftOpened.value
);

const shiftOpenPromptVisible = computed(() => showShiftOpeningPanel.value);

const canConfirmShiftOpen = computed(() => {
  if (!operator.value || !supervisor.value || !coordinator.value || !shiftPreviewBatch.value) {
    return false;
  }
  if (shiftReopenRequired.value) {
    if (!shiftReopenReason.value) {
      return false;
    }
    if (shiftReopenReason.value === "Other" && !shiftReopenRemarks.value.trim()) {
      return false;
    }
  }
  return true;
});

function isCompleteGsmRoll(r) {
  return (
    !!r &&
    !r.is_bundle_row &&
    !r.is_wasted &&
    !!r.batch_no &&
    sprFlt(r.produced_length_mtrs) > 0 &&
    sprNormalizeGrossWeightInput(r.gross_weight) > 0
  );
}

const fabricRollLines = computed(() => rollLines.value || []);

const submitConfirmRolls = computed(() =>
  rollLines.value.filter((r) => isCompleteGsmRoll(r))
);

const submitIncompleteRolls = computed(() =>
  fabricRollLines.value.filter((r) => !r.is_wasted && !r.is_bundle_row && !isCompleteGsmRoll(r))
);

const submitConfirmNetKg = computed(() =>
  submitConfirmRolls.value.reduce((s, r) => s + sprFlt(r.net_weight), 0)
);

const submitConfirmGrossKg = computed(() =>
  submitConfirmRolls.value.reduce((s, r) => s + sprNormalizeGrossWeightInput(r.gross_weight), 0)
);

const submitOrderSummary = computed(() => {
  const byOrder = new Map();
  for (const r of submitConfirmRolls.value) {
    const k = r.party_code || r.order_code || r.pp_id || "—";
    if (!byOrder.has(k)) {
      byOrder.set(k, { orderCode: k, produced: 0 });
    }
    byOrder.get(k).produced += sprFlt(r.net_weight);
  }
  return [...byOrder.values()];
});

const canOpenWastageRecycle = computed(
  () => shiftOpened.value && wastageRecycleSprList.value.length > 0
);

const canOpenQualityCheck = computed(
  () => shiftOpened.value && sessionSprList.value.length > 0
);

const canOpenLotSample = computed(
  () =>
    shiftOpened.value &&
    !!(headerUnit.value || filterUnit.value) &&
    !!runDate.value &&
    !!shift.value
);

const canOpenShiftConsumables = computed(
  () =>
    shiftOpened.value &&
    !!(headerUnit.value || filterUnit.value) &&
    !!runDate.value &&
    !!shift.value
);

const MIXING_EXCLUDED_UNITS = [
  "TTT- L3 - OYANG C900 BAG MAKING LINE",
  "TTT- L2 - OYANG C700 BAG MAKING LINE",
  "TTT- L1 - OYANG C700 BAG MAKING LINE",
  "VTP-L1 LEADER OYANG MACHINE",
  "VTP-L2 LEADER ZX MACHINE",
  "JVE-L3 B700 BAG MAKING MACHINE",
  "JVE-L2 B700 BAG MAKING MACHINE",
  "JVE-L1 B700 BAG MAKING MACHINE",
];

const isMixingExcluded = computed(() =>
  MIXING_EXCLUDED_UNITS.includes((headerUnit.value || filterUnit.value || "").trim())
);

const canOpenMixingSheet = computed(
  () =>
    !!(headerUnit.value || filterUnit.value) &&
    !!runDate.value &&
    !!shift.value &&
    !isMixingExcluded.value
);

function formatMixPlanningKey(key) {
  const k = _cstr(key);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let m = k.match(/^month-(\d{4})-(\d{1,2})/);
  if (m) {
    return `${MONTHS[Number(m[2]) - 1] || m[2]} ${m[1]}`;
  }
  m = k.match(/^day-(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (m) {
    return `${MONTHS[Number(m[2]) - 1] || m[2]} ${Number(m[3])}, ${m[1]}`;
  }
  m = k.match(/^week-(\d{4})-W(\d{1,2})/);
  if (m) {
    return `Week ${Number(m[2])} · ${m[1]}`;
  }
  return k || "—";
}

async function loadMixRollCandidates(options = {}) {
  const unit = headerUnit.value || filterUnit.value;
  if (!unit) {
    mixRollCandidates.value = [];
    return;
  }
  mixRollLoading.value = true;
  try {
    const res = await fetchGsmMixRollCandidates(unit, 0, {
      plannedDate: ordersBrowseDate(),
      viewScope: viewScope.value,
      filterWeek: filterWeek.value,
      filterMonth: filterMonth.value,
    });
    mixRollCandidates.value = res.mix_rolls || [];
    if (!options.skipRestore) {
      await restoreActiveMixRollFromSession();
    }
  } catch (e) {
    console.error(e);
    mixRollCandidates.value = [];
  } finally {
    mixRollLoading.value = false;
  }
}

const addMixRollColorOptions = computed(() => {
  const set = new Set(MIX_COLOR_OPTIONS);
  for (const c of addMixRollExtraColors.value || []) {
    const v = String(c || "").trim().toUpperCase();
    if (v) set.add(v);
  }
  return Array.from(set).sort();
});

const addMixRollShaftHint = computed(() =>
  mixShaftHint(addMixRollForm.value.unit, addMixRollMaxShaft.value)
);

const canSubmitAddMixRoll = computed(() => {
  const f = addMixRollForm.value;
  return !!(
    f.unit &&
    f.color1 &&
    f.color2 &&
    String(f.mixName || "").trim() &&
    f.quality &&
    f.clType &&
    Number(f.gsm) > 0 &&
    String(f.shaft || "").trim()
  );
});

function mixBrowseArgs() {
  return {
    plannedDate: ordersBrowseDate(),
    viewScope: viewScope.value,
    filterWeek: filterWeek.value,
    filterMonth: filterMonth.value,
    runDate: runDate.value,
  };
}

function emptyAddMixRollForm(unit) {
  return {
    unit: unit || "",
    color1: "",
    color2: "",
    mixName: "",
    quality: "Virgin Mix",
    clType: "Color Mix",
    gsm: "",
    shaft: "",
    noOfShaft: 1,
    kg: 0,
  };
}

function onAddMixRollColorChange() {
  const f = addMixRollForm.value;
  if (f.color1 && f.color2) {
    f.clType = suggestMixClType(f.color1, f.color2);
  }
}

async function openAddMixRollDialog() {
  const unit = headerUnit.value || filterUnit.value;
  if (!unit) {
    frappe.msgprint(__("Start the shift so the unit is set, then add a mix roll."));
    return;
  }
  addMixRollForm.value = emptyAddMixRollForm(unit);
  addMixRollMaxShaft.value = 0;
  addMixRollExtraColors.value = [];
  showAddMixRollDialog.value = true;
  try {
    const opts = await fetchGsmMixRollFormOptions(unit, mixBrowseArgs());
    addMixRollMaxShaft.value = Number(opts.max_shaft_inches) || 0;
    addMixRollExtraColors.value = opts.colors || [];
    if (opts.unit) {
      addMixRollForm.value.unit = opts.unit;
    }
  } catch (e) {
    console.error(e);
  }
}

function closeAddMixRollDialog() {
  if (addMixRollBusy.value) {
    return;
  }
  showAddMixRollDialog.value = false;
}

async function submitAddMixRoll() {
  if (!canSubmitAddMixRoll.value || addMixRollBusy.value) {
    return;
  }
  const f = addMixRollForm.value;
  addMixRollBusy.value = true;
  try {
    const browse = mixBrowseArgs();
    const res = await upsertGsmMixRollFromEntry({
      unit: f.unit,
      color1: f.color1,
      color2: f.color2,
      mix_name: String(f.mixName || "").trim(),
      quality: f.quality,
      cl_type: f.clType,
      gsm: f.gsm,
      shaft: String(f.shaft || "").trim(),
      no_of_shaft: f.noOfShaft || 1,
      kg: f.kg || 0,
      planned_date: browse.plannedDate,
      view_scope: browse.viewScope,
      filter_week: browse.filterWeek,
      filter_month: browse.filterMonth,
      run_date: browse.runDate,
    });
    showAddMixRollDialog.value = false;
    await loadMixRollCandidates({ skipRestore: true });
    const created = res.mix || {};
    const createdId = created.mix_id || created.mix_row_key;
    if (createdId && !mixRollCandidates.value.some((m) => (m.mix_id || m.mix_row_key) === createdId)) {
      mixRollCandidates.value = [created, ...mixRollCandidates.value];
    }
    const action = res.action === "updated" ? "updated on Color Chart" : "saved to Color Chart";
    frappe.show_alert({
      message: __("Mix roll {0}. Select the card to enter production.", [action]),
      indicator: "green",
    });
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not add mix roll. Check Quality / Color masters, GSM and shaft width."));
  } finally {
    addMixRollBusy.value = false;
  }
}

async function isMixSprDraft(sprName) {
  const name = _cstr(sprName);
  if (!name) {
    return false;
  }
  const fromCandidates = mixRollCandidates.value.find((m) => m.spr_name === name);
  if (fromCandidates) {
    if (cint(fromCandidates.spr_docstatus) === 1 || fromCandidates._submitted) {
      return false;
    }
    if (fromCandidates.spr_docstatus === 0 || fromCandidates.spr_status === "Draft") {
      return true;
    }
  }
  try {
    const headers = await gsmSprHeaders([name]);
    return cint(headers[name]?.docstatus) === 0;
  } catch (e) {
    return true;
  }
}

async function restoreActiveMixRollFromSession() {
  if (!shiftOpened.value) {
    return;
  }
  const currentUnit = _cstr(headerUnit.value || filterUnit.value);
  const currentSpr = _cstr(activeMixRoll.value?.spr_name);
  if (currentSpr && !dismissedMixSprs.value.includes(currentSpr)) {
    await refreshMixRollLinesFromSpr();
    return;
  }
  const mixRow = rollLines.value.find((r) => r.is_mix_roll_row && r.spr_name);
  const savedSpr = _cstr(persistedActiveMixSpr.value);
  const sprName = _cstr(mixRow?.spr_name || savedSpr);
  if (!sprName || dismissedMixSprs.value.includes(sprName)) {
    return;
  }
  let mixMeta = mixRollCandidates.value.find((m) => m.spr_name === sprName);
  if (
    mixMeta &&
    _cstr(mixMeta.unit || mixMeta.custom_unit) &&
    currentUnit &&
    !sameGsmUnit(mixMeta.unit || mixMeta.custom_unit, currentUnit)
  ) {
    return;
  }
  if (!mixMeta) {
    mixMeta = {
      spr_name: sprName,
      label: mixRow?.party_code || "Mix Roll",
      gsm: mixRow?.gsm,
      color_transition: mixRow?.color || "",
      shaft: String(mixRow?.width_inch || ""),
      date_key: "",
      custom_unit: mixRowUnit(mixRow) || currentUnit,
      unit: mixRowUnit(mixRow) || currentUnit,
    };
  }
  if (sprName) {
    try {
      const headers = await gsmSprHeaders([sprName]);
      const sprUnit = _cstr(headers[sprName]?.custom_unit);
      if (sprUnit && currentUnit && !sameGsmUnit(sprUnit, currentUnit)) {
        return;
      }
    } catch (e) {
      console.warn("mix spr unit", e);
    }
  }
  const sprDate = _cstr(mixMeta.spr_run_date).slice(0, 10);
  const run = _cstr(runDate.value).slice(0, 10);
  if (!mixRow && sprDate && run && sprDate !== run) {
    return;
  }
  if (!mixRow && mixMeta.spr_shift && _cstr(mixMeta.spr_shift) !== _cstr(shift.value)) {
    return;
  }
  if (!(await isMixSprDraft(mixMeta.spr_name))) {
    return;
  }
  activeMixRoll.value = { ...mixMeta, spr_name: mixMeta.spr_name, custom_unit: currentUnit };
  persistedActiveMixSpr.value = mixMeta.spr_name;
  await refreshMixRollLinesFromSpr();
}

async function refreshMixRollLinesFromSpr() {
  if (!activeMixRoll.value?.spr_name) {
    mixRollLines.value = [];
    return;
  }
  try {
    const res = await loadGsmMixRollSprRolls(activeMixRoll.value.spr_name);
    const sprUnit = _cstr(res.custom_unit);
    const unitNow = _cstr(headerUnit.value || filterUnit.value);
    const rows = (res.roll_lines || []).map((line) =>
      mapMixRollLineFromServer(
        { ...line, spr_name: res.spr_name, custom_unit: unitNow || sprUnit },
        { ...activeMixRoll.value, custom_unit: unitNow || sprUnit }
      )
    );
    if (!rows.length) {
      return;
    }
    attachMixRowsToMainGrid(rows);
  } catch (e) {
    console.error(e);
  }
}

function mixRowsForActive() {
  const spr = activeMixRoll.value?.spr_name;
  return mixGridRowsForSpr(rollLines.value, spr);
}

function mixProducedRolls(mix) {
  if (activeMixRoll.value?.spr_name && mix?.spr_name === activeMixRoll.value.spr_name) {
    return mixProducedRollCount(mix, rollLines.value);
  }
  return mixProducedRollCount(mix, []);
}

function mixProducedShafts(mix) {
  if (activeMixRoll.value?.spr_name && mix?.spr_name === activeMixRoll.value.spr_name) {
    return mixProducedShaftCount(mix, rollLines.value);
  }
  const segs = Math.max(1, mixRollWidthOptions(mix).length);
  const rolls = mixProducedRolls(mix);
  if (rolls <= 0) {
    return 0;
  }
  return Math.min(mixRollShaftCount(mix), Math.ceil(rolls / segs));
}

function mixRollRowKey(row) {
  return `${_cstr(row?.spr_name)}::${_cstr(row?.spr_item_name || row?.batch_no)}`;
}

function copyMixRollValues(target, source) {
  if (!target || !source || target === source) {
    return target;
  }
  Object.assign(target, {
    produced_length_mtrs: source.produced_length_mtrs,
    produced_gsm: source.produced_gsm,
    net_weight: source.net_weight,
    gross_weight: source.gross_weight,
    planned_qty: source.is_mix_roll_row ? 0 : source.planned_qty,
    custom_core_width_mm: source.custom_core_width_mm,
    custom_polybag_kgs: source.custom_polybag_kgs,
    custom_diameter_inches: source.custom_diameter_inches,
    custom_cbm_cubic_meters: source.custom_cbm_cubic_meters,
    row_locked: source.row_locked,
    row_ready_for_print: source.row_ready_for_print,
    spr_item_name: source.spr_item_name,
  });
  return target;
}

function syncMixRollRowViews(source) {
  if (!source?.is_mix_roll_row) {
    return;
  }
  const key = mixRollRowKey(source);
  for (const row of [...mixRollLines.value, ...rollLines.value]) {
    if (row !== source && mixRollRowKey(row) === key) {
      copyMixRollValues(row, source);
    }
  }
}

function attachMixRowsToMainGrid(rows) {
  const attached = [];
  for (const incoming of rows || []) {
    incoming.is_mix_roll_row = 1;
    incoming.custom_unit = incoming.custom_unit || headerUnit.value || filterUnit.value || "";
    const key = mixRollRowKey(incoming);
    const existing = rollLines.value.find((row) => row.is_mix_roll_row && mixRollRowKey(row) === key);
    if (existing) {
      const keepSeq = cint(existing.creation_seq);
      const keepId = existing._id;
      Object.assign(existing, incoming);
      existing._id = keepId || existing._id;
      existing.is_mix_roll_row = 1;
      if (keepSeq) {
        existing.creation_seq = keepSeq;
      }
      attached.push(existing);
    } else {
      rollLines.value = sortRollLinesLifo([incoming, ...rollLines.value]);
      attached.push(incoming);
    }
  }
  mixRollLines.value = attached;
}

async function startMixRollProduction(mix) {
  if (!shiftOpened.value) {
    frappe.msgprint(__("Open shift before starting mix roll production."));
    return;
  }
  mixRollBusy.value = true;
  try {
    const res = await activateGsmMixRollForSession({
      dateKey: mix.date_key,
      mixId: mix.mix_id,
      mixRowKey: mix.mix_row_key,
      runDate: runDate.value,
      shift: shift.value,
      unit: headerUnit.value || filterUnit.value,
    });
    activeMixRoll.value = {
      ...mix,
      ...(res.mix || {}),
      spr_name: res.spr_name,
      custom_unit: headerUnit.value || filterUnit.value,
      unit: headerUnit.value || filterUnit.value,
    };
    dismissedMixSprs.value = dismissedMixSprs.value.filter((n) => n !== res.spr_name);
    persistedActiveMixSpr.value = res.spr_name;
    const rows = (res.roll_lines || []).map((line) =>
      mapMixRollLineFromServer({ ...line, spr_name: res.spr_name }, activeMixRoll.value)
    );
    attachMixRowsToMainGrid(rows);
    await loadMixRollCandidates({ skipRestore: true });
    persistDraft();
    frappe.show_alert({ message: __("Mix roll SPR ready: {0}", [res.spr_name]), indicator: "green" });
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not start mix roll production."));
  } finally {
    mixRollBusy.value = false;
  }
}

function clearActiveMixRoll() {
  const spr = _cstr(activeMixRoll.value?.spr_name);
  if (spr && !dismissedMixSprs.value.includes(spr)) {
    dismissedMixSprs.value = [...dismissedMixSprs.value, spr];
  }
  activeMixRoll.value = null;
  persistedActiveMixSpr.value = "";
  mixRollLines.value = [];
  rollLines.value = rollLines.value.filter((r) => !r.is_mix_roll_row);
  scheduleAutosave();
}

async function removeMixRollRow(row) {
  if (!row?.is_mix_roll_row) {
    return;
  }
  const sprName = row.spr_name || activeMixRoll.value?.spr_name;
  const key = mixRollRowKey(row);
  const remove = async () => {
    mixRollBusy.value = true;
    try {
      if (sprName && (row.spr_item_name || row.row_locked)) {
        await frappe.call({
          method: "production_entry.production_planning.unified_production_entry_api.delete_gsm_roll_line",
          args: {
            spr_name: sprName,
            batch_no: row.batch_no || undefined,
            row_name: row.spr_item_name || undefined,
          },
        });
      }
      mixRollLines.value = mixRollLines.value.filter((r) => mixRollRowKey(r) !== key);
      rollLines.value = rollLines.value.filter((r) => mixRollRowKey(r) !== key);
      releaseBatchNo(row.batch_no);
      syncBatchCounterFromGrid();
      scheduleAutosave();
      saveStatus.value = row.spr_item_name ? "Removed from SPR" : "Row removed";
    } catch (e) {
      console.error(e);
      frappe.msgprint(__("Could not remove mix roll row from {0}.", [sprName || "SPR"]));
    } finally {
      mixRollBusy.value = false;
    }
  };
  if (row.spr_item_name || row.row_locked) {
    frappe.confirm(__("Remove this mix roll row from the grid and delete it from {0}?", [sprName]), remove);
  } else {
    await remove();
  }
}

function onMixRowEdit(row) {
  coerceProducedLengthMtrs(row);
  recalcMixRollRow(row);
  syncMixRollRowViews(row);
  scheduleAutosave();
}

async function addMixRollRow() {
  if (!activeMixRoll.value?.spr_name) {
    frappe.msgprint(__("Start mix roll production first."));
    return;
  }
  const existing = mixRowsForActive();
  const maxRows = mixRollMaxRows(activeMixRoll.value);
  if (existing.length >= maxRows) {
    frappe.msgprint(
      __("This mix roll allows {0} shaft(s) / {1} rolls with combination {2}. {3} roll(s) already entered.", [
        mixRollShaftCount(activeMixRoll.value),
        maxRows,
        activeMixRoll.value.shaft || activeMixRoll.value.combination || "",
        existing.length,
      ])
    );
    return;
  }
  const slot = nextMixRollCombinationSlot(activeMixRoll.value, existing);
  const itemCode = slot.itemCode || mixRollItemOptions(activeMixRoll.value)[0]?.item_code || "";
  const widthInch = slot.widthInch || mixRollWidthOptions(activeMixRoll.value)[0] || 0;
  if (!itemCode) {
    frappe.msgprint(__("No item codes on this mix row."));
    return;
  }
  mixRollBusy.value = true;
  try {
    const sprName = activeMixRoll.value.spr_name;
    let batch = null;
    if (sprName) {
      const gsmPrefix = _cstr(seriesPrefix.value || shiftBatchPrefix.value);
      syncBatchCounterFromGrid();
      const existing = allExistingBatchNos();
      const res = await frappe.call({
        method:
          "production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_next_spr_batch_numbers",
        args: {
          shaft_production_run: sprName,
          count: 1,
          client_max_roll: maxRollSuffix.value,
          run_date: runDate.value,
          custom_unit: headerUnit.value,
          shift: shift.value,
          client_series_prefix: gsmPrefix || undefined,
          existing_batches: JSON.stringify(existing),
          gsm_shift_prefix: 1,
        },
      });
      batch = (res.message || [])[0];
    } else {
      batch = await previewNextBatch(null);
    }
    if (!batch?.batch_no) {
      frappe.msgprint(__("Could not assign batch number."));
      return;
    }
    const line = mapMixRollLineFromServer(
      {
        spr_name: activeMixRoll.value.spr_name,
        item_code: itemCode,
        width_inch: widthInch,
        batch_no: batch.batch_no,
        roll_no: batch.roll_no,
        gsm: activeMixRoll.value.gsm,
        job_id: "1",
        row_locked: 0,
        row_ready_for_print: 0,
      },
      activeMixRoll.value
    );
    line.batch_no = batch.batch_no;
    line.roll_no = batch.roll_no || line.roll_no;
    line.party_code = activeMixRoll.value.label || line.party_code;
    line.gsm = activeMixRoll.value.gsm || line.gsm;
    line.quality = activeMixRoll.value.quality || line.quality;
    line.color = line.color || activeMixRoll.value.color_transition || "";
    line.width_inch = widthInch || line.width_inch;
    line.item_code = itemCode;
    if (slot.itemName) {
      line.item_name = slot.itemName;
    }
    line.creation_seq = nextCreationSeq();
    const segs = Math.max(1, mixRollWidthOptions(activeMixRoll.value).length);
    line.custom_no_of_shaft = Math.floor(mixRowsForActive().length / segs) + 1;
    if (sprName) {
      try {
        const added = await addGsmMixRollLine({
          sprName,
          itemCode,
          widthInch,
          batchNo: batch.batch_no,
          gsm: activeMixRoll.value.gsm,
        });
        if (added?.row_name) {
          line.spr_item_name = added.row_name;
        }
        if (added?.roll_line?.batch_no) {
          line.batch_no = added.roll_line.batch_no;
        }
      } catch (apiErr) {
        console.warn("add_gsm_mix_roll_line", apiErr);
      }
    }
    mixRollLines.value = [line, ...mixRollLines.value];
    rollLines.value = sortRollLinesLifo([line, ...rollLines.value]);
    reserveBatchNo(batch.batch_no, batch.roll_no);
    scheduleAutosave();
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not add mix roll row."));
  } finally {
    mixRollBusy.value = false;
  }
}

async function saveMixRollRow(row) {
  const sprName = row?.spr_name || activeMixRoll.value?.spr_name;
  if (!sprName) {
    return;
  }
  const gross = sprNormalizeGrossWeightInput(row.gross_weight);
  if (gross <= 0) {
    frappe.msgprint(__("Enter gross weight before saving."));
    return;
  }
  if (!row.batch_no) {
    frappe.msgprint(__("Batch number is required."));
    return;
  }
  if (!sprFlt(row.produced_length_mtrs)) {
    frappe.msgprint(__("Enter produced length before saving."));
    return;
  }
  recalcMixRollRow(row);
  mixRollBusy.value = true;
  try {
    const payload = buildMixRollSavePayload(row);
    payload.party_code = activeMixRoll.value?.label || payload.party_code;
    const res = await saveGsmMixRollLine({
      sprName,
      shift: shift.value,
      rollPayload: payload,
    });
    row.row_locked = 1;
    row.row_ready_for_print = 1;
    if (res.row_name) {
      row.spr_item_name = res.row_name;
    }
    const saved = res.roll_line || {};
    if (saved.net_weight != null) {
      row.net_weight = saved.net_weight;
    }
    if (saved.produced_gsm != null) {
      row.produced_gsm = saved.produced_gsm;
    }
    if (saved.gross_weight != null && saved.gross_weight !== "") {
      row.gross_weight = String(saved.gross_weight);
    }
    if (saved.custom_diameter_inches != null || saved.custom_diameter != null) {
      const dia = normalizeSpiDiameterCbm(saved);
      row.custom_diameter_inches = dia.custom_diameter_inches;
      row.custom_cbm_cubic_meters = dia.custom_cbm_cubic_meters || row.custom_cbm_cubic_meters;
    } else if (saved.custom_cbm != null || saved.custom_cbm_cubic_meters != null) {
      const dia = normalizeSpiDiameterCbm(saved);
      row.custom_cbm_cubic_meters = dia.custom_cbm_cubic_meters;
    }
    if (saved.custom_core_width_mm != null) {
      row.custom_core_width_mm = saved.custom_core_width_mm;
    }
    if (saved.custom_polybag_kgs != null) {
      row.custom_polybag_kgs = saved.custom_polybag_kgs;
    }
    row.planned_qty = 0;
    syncMixRollRowViews(row);
    persistDraft();
    scheduleAutosave();
    frappe.show_alert({ message: __("Mix roll row saved"), indicator: "green" });
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not save mix roll row."));
  } finally {
    mixRollBusy.value = false;
  }
}

function preferredSprPickContext() {
  const key = lastAddRollJobKey.value;
  if (key && key.includes("::")) {
    const ppId = key.split("::")[0];
    if (ppId) {
      return { pp_id: ppId };
    }
  }
  const top = rollLines.value.find((r) => !r.is_bundle_row && !r.is_wasted && r.pp_id);
  if (top?.pp_id) {
    return { pp_id: top.pp_id };
  }
  return {};
}

function openMixingDialog() {
  openGsmMixingSheetDialog({
    headerUnit: headerUnit.value || filterUnit.value,
    runDate: runDate.value,
    shift: shift.value,
    shiftSessionId: shiftSession.value?.name || "",
  });
}

function openBreakdownDialog() {
  openGsmBreakdownDialog({
    headerUnit: headerUnit.value || filterUnit.value,
    runDate: runDate.value,
    shift: shift.value,
    shiftSessionId: shiftSession.value?.name || "",
  });
}

function openLotSampleDialog() {
  openGsmLotSampleDialog({
    headerUnit: headerUnit.value || filterUnit.value,
    runDate: runDate.value,
    shift: shift.value,
    shiftSessionId: shiftSession.value?.name || "",
    sessionSprList: wastageRecycleSprList.value,
    selectedPpIds: selectedEntries.value.map((e) => e.ppId).filter(Boolean),
  });
}

function openShiftConsumablesDialog() {
  openGsmShiftConsumablesDialog({
    headerUnit: headerUnit.value || filterUnit.value,
    runDate: runDate.value,
    shift: shift.value,
    shiftSessionId: shiftSession.value?.name || "",
  });
}

function handleRollWasted(roll, sprRow) {
  const batch = roll?.batch_no;
  const row = rollLines.value.find(
    (r) =>
      (batch && r.batch_no === batch) ||
      (roll?.spr_item_name && r.spr_item_name === roll.spr_item_name)
  );
  if (row) {
    row.is_wasted = true;
    row.row_locked = true;
    row.row_readonly = true;
  }
  refreshSessionFromServer({ quiet: true, merge: false });
}

function openWastageDialog() {
  if (!wastageRecycleSprList.value.length) {
    frappe.msgprint(__("Create SPRs for this shift before opening Wastage."));
    return;
  }
  openGsmWastageDialog({
    sessionSprList: wastageRecycleSprList.value,
    rollLines: rollLines.value,
    onRollWasted: handleRollWasted,
    laminationMode: !!isLaminationMode.value,
    ...preferredSprPickContext(),
  });
}

function openRecycleDialog() {
  if (!wastageRecycleSprList.value.length) {
    frappe.msgprint(__("Create SPRs for this shift before opening Recycle."));
    return;
  }
  openGsmRecycleDialog({
    sessionSprList: wastageRecycleSprList.value,
    ...preferredSprPickContext(),
  });
}

function shiftRollLines(summary) {
  if (!summary) {
    return [];
  }
  if (summary.roll_lines?.length) {
    return summary.roll_lines;
  }
  const lines = [];
  for (const spr of summary.spr_list || []) {
    for (const r of spr.rolls || []) {
      lines.push({
        ...r,
        spr_name: spr.spr_name,
        spr_status: spr.spr_status,
        party_code: r.party_code,
        job_id: r.job_id || "",
        quality: r.quality || "",
        color: r.color || "",
      });
    }
  }
  return lines;
}

function formatNetWeightDisplay(val) {
  if (val === null || val === undefined || val === "") {
    return "—";
  }
  if (typeof val === "string" && val.includes("+")) {
    return val;
  }
  return formatKg(val);
}

const shiftStatusChips = computed(() => {
  const map = shiftStatusByShift.value || {};
  return ["Day Shift", "Night Shift"].map((shiftName) => {
    const row = map[shiftName] || {};
    const status = row.status || "Not started";
    let tone = "muted";
    let label = status;
    if (status === "Open") {
      tone = shiftName === shift.value ? "open" : "other-open";
      label = row.batch_series_prefix ? `Open · ${row.batch_series_prefix}` : "Open";
    } else if (status === "Closed") {
      tone = "closed";
      label = row.batch_series_prefix ? `Closed · ${row.batch_series_prefix}` : "Closed";
    }
    return { shift: shiftName, label, tone };
  });
});

const canCreateSprs = computed(
  () =>
    shiftOpened.value &&
    !allSprsCreated.value &&
    selectionLocked.value &&
    selectedEntries.value.length > 0 &&
    headerUnit.value &&
    runDate.value &&
    shift.value
);

const canSubmitEntry = computed(() => {
  if (!shiftOpened.value || submitInProgress.value) {
    return false;
  }
  if (!allSubmitSprList.value.length || !submitConfirmRolls.value.length) {
    return false;
  }
  // Completeness only — Save Row is enforced in openSubmitConfirmDialog / submitEntry
  // so the button stays clickable and can auto-save unsaved lines.
  return submitConfirmRolls.value.every(
    (r) =>
      (r.is_mix_roll_row ? r.spr_name : sprNameForPp(r.pp_id)) &&
      r.batch_no &&
      sprNormalizeGrossWeightInput(r.gross_weight) > 0
  );
});

const allSubmitRollsSaved = computed(() =>
  submitConfirmRolls.value.every((r) => r.row_locked || r.spr_item_name)
);

const toleranceFormComplete = computed(() => {
  if (!toleranceOrders.value.length) {
    return false;
  }
  return toleranceOrders.value.every((o) => {
    const f = toleranceForm.value[o.spr_name] || {};
    return (f.reason || "").trim() && f.approved;
  });
});

function jobRowClass(job) {
  return {
    selected: isJobSelected(job),
    "gpe-line-disabled": !job.selectable,
  };
}

function isJobSelected(job) {
  return selectedEntryKeys.value.has(entryKeyJob(job.pp_id, job.job_id));
}

function toggleJob(job, ev) {
  if (!shiftOpened.value && ev.target.checked) {
    ev.target.checked = false;
    frappe.msgprint(__("Start the shift before selecting jobs."));
    return;
  }
  if (!job?.selectable && ev.target.checked) {
    ev.target.checked = false;
    return;
  }
  const snap = snapshotFromJob(job);
  const checked = ev.target.checked;

  if (selectionLocked.value) {
    if (!checked) {
      ev.target.checked = true;
      return;
    }
    if (!selectedEntryKeys.value.has(snap.key)) {
      selectedEntries.value = [...selectedEntries.value, snap];
      loadJobBoard().then(() => enrichSelectedEntriesFromBoard());
      scheduleAutosave();
      scheduleJobSelectionSave();
    }
    return;
  }

  const next = selectedEntries.value.filter((e) => e.key !== snap.key);
  if (checked) {
    next.push(snap);
    if (sessionSprIsSubmitted(snap.ppId)) {
      forceNewSprByPp.value = { ...forceNewSprByPp.value, [snap.ppId]: true };
    }
  }
  selectedEntries.value = next;
  scheduleAutosave();
}

function onJobLabelClick(job) {
  if (!shiftOpened.value) {
    frappe.msgprint(__("Start the shift before selecting jobs."));
    return;
  }
  if (!job.selectable) {
    return;
  }
  if (selectionLocked.value) {
    if (isJobSelected(job)) {
      return;
    }
    const snap = snapshotFromJob(job);
    if (!selectedEntryKeys.value.has(snap.key)) {
      selectedEntries.value = [...selectedEntries.value, snap];
      loadJobBoard().then(() => enrichSelectedEntriesFromBoard());
      scheduleAutosave();
      scheduleJobSelectionSave();
    }
    return;
  }
  const snap = snapshotFromJob(job);
  const next = selectedEntries.value.filter((e) => e.key !== snap.key);
  if (!isJobSelected(job)) {
    next.push(snap);
    if (sessionSprIsSubmitted(snap.ppId)) {
      forceNewSprByPp.value = { ...forceNewSprByPp.value, [snap.ppId]: true };
    }
  }
  selectedEntries.value = next;
  scheduleAutosave();
}

function lineRowClass(line) {
  return {
    selected: isEntrySelected(line),
    "gpe-line-disabled": !line.selectable,
  };
}

function toggleLine(line, ev) {
  if (!line?.selectable && ev.target.checked) {
    ev.target.checked = false;
    return;
  }
  const snap = snapshotFromLine(line);
  const checked = ev.target.checked;

  if (selectionLocked.value) {
    if (!checked) {
      ev.target.checked = true;
      return;
    }
    if (!selectedEntryKeys.value.has(snap.key)) {
      selectedEntries.value = [...selectedEntries.value, snap];
      scheduleAutosave();
    }
    return;
  }

  const next = selectedEntries.value.filter((e) => e.key !== snap.key);
  if (checked) {
    next.push(snap);
  }
  selectedEntries.value = next;
  scheduleAutosave();
}

function onLineLabelClick(line) {
  if (!line.selectable) {
    return;
  }
  if (selectionLocked.value) {
    if (isEntrySelected(line)) {
      return;
    }
    const snap = snapshotFromLine(line);
    if (!selectedEntryKeys.value.has(snap.key)) {
      selectedEntries.value = [...selectedEntries.value, snap];
      scheduleAutosave();
    }
    return;
  }
  const snap = snapshotFromLine(line);
  const next = selectedEntries.value.filter((e) => e.key !== snap.key);
  if (!isEntrySelected(line)) {
    next.push(snap);
  }
  selectedEntries.value = next;
  scheduleAutosave();
}

function openConfirmSelection() {
  if (!shiftOpened.value) {
    frappe.msgprint(__("Start the shift before confirming job selection."));
    return;
  }
  if (!selectedEntries.value.length) {
    return;
  }
  showConfirmDialog.value = true;
}

function confirmSelection() {
  recordJobApiBaselines(jobBoardJobs.value);
  selectionLocked.value = true;
  showConfirmDialog.value = false;
  scheduleAutosave();
  scheduleJobSelectionSave();
}

function unlockSelection() {
  if (rollLines.value.length) {
    frappe.confirm(
      __("Unlock selection? Existing roll rows stay in the grid. Click <b>Confirm selection</b> again before adding new rows."),
      () => {
        selectionLocked.value = false;
        scheduleAutosave();
        scheduleJobSelectionSave();
      }
    );
    return;
  }
  selectionLocked.value = false;
  scheduleAutosave();
  scheduleJobSelectionSave();
}

function clearSelection() {
  if (selectionLocked.value) {
    frappe.msgprint("Unlock selection first.");
    return;
  }
  selectedEntries.value = [];
  scheduleAutosave();
  scheduleJobSelectionSave();
}

async function openShaftDetails() {
  const unit = headerUnit.value || filterUnit.value;
  if (!unit) {
    frappe.msgprint(__("Select a unit first."));
    return;
  }
  if (!shiftOpened.value && selectionLocked.value && batchContextKey.value !== currentBatchContextKey()) {
    clearGsmUnitContextState();
    frappe.msgprint(__("Job selection was from another unit/shift — select jobs again for {0}.", [unit]));
    return;
  }
  const ppIds = [...new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean))];
  if (!ppIds.length) {
    frappe.msgprint(__("Select orders first."));
    return;
  }
  showShaftDetailsDialog.value = true;
  shaftDetailsLoading.value = true;
  shaftDetailsBlocks.value = [];
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_pp_shaft_details",
      args: { pp_ids: JSON.stringify(ppIds) },
    });
    shaftDetailsBlocks.value = (res.message || []).filter((b) => b.status === "ok");
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not load shaft details."));
  } finally {
    shaftDetailsLoading.value = false;
  }
}

function openShiftTab() {
  pageTab.value = "shift";
  shiftFilterDate.value = runDate.value;
  shiftFilterShift.value = shift.value;
  shiftFilterUnit.value = filterUnit.value || headerUnit.value || shiftFilterUnit.value;
  loadShiftEntries();
}

function openSummaryShiftTab() {
  summaryTab.value = "shiftSummary";
  summaryShiftDate.value = runDate.value;
  summaryShiftShift.value = shift.value;
  summaryShiftUnit.value = filterUnit.value || headerUnit.value || summaryShiftUnit.value;
  loadSummaryShiftSummary();
}

function shiftSessionStatusClass(status) {
  if (status === "Open") {
    return "gpe-chip-submitted";
  }
  if (status === "Closed") {
    return "gpe-chip-closed";
  }
  return "gpe-chip-draft";
}

function sprStatusChipClass(status) {
  return status === "Submitted" ? "gpe-chip-submitted" : "gpe-chip-draft";
}

function closeToolsMenu() {
  toolsMenuOpen.value = false;
}

function closeQualityMenu() {
  qualityMenuOpen.value = false;
}

function toggleQualityMenu() {
  qualityMenuOpen.value = !qualityMenuOpen.value;
  toolsMenuOpen.value = false;
}

function pickToolOrder(options) {
  return new Promise((resolve) => {
    frappe.prompt(
      [
        {
          fieldtype: "Select",
          fieldname: "pp_id",
          label: __("Order / SPR"),
          options: options.map((o) => ({ value: o.ppId, label: `${o.orderCode} · ${o.spr_name}` })),
          reqd: 1,
        },
      ],
      (values) => {
        const chosen = options.find((o) => o.ppId === values.pp_id);
        resolve(chosen || options[0]);
      },
      __("Choose order for SPR Tools"),
      __("Open")
    );
  });
}

async function resolveToolContext() {
  const ctx = toolsContext.value;
  if (!ctx) {
    return null;
  }
  if (!ctx.multi) {
    return ctx;
  }
  const chosen = await pickToolOrder(ctx.options);
  if (!chosen) {
    return null;
  }
  return { ppId: chosen.ppId, planningNames: chosen.lineIds, orderCode: chosen.orderCode };
}

async function attachTrialSprToSession(result) {
  const sprName = String(result?.shaft_production_run || "").trim();
  const jobId = String(result?.job_id || "").trim();
  const orderCode = String(result?.order_code || "").trim();
  if (!sprName) {
    await fetchOrders();
    return;
  }
  sessionSprs.value = {
    ...sessionSprs.value,
    [sprName]: {
      pp_id: sprName,
      spr_name: sprName,
      order_code: orderCode,
      is_trial: 1,
    },
  };
  if (jobId) {
    const key = entryKeyJob(sprName, jobId);
    if (!selectedEntries.value.some((e) => e.key === key || (e.ppId === sprName && String(e.jobId || e.job_id) === jobId))) {
      selectedEntries.value = [
        ...selectedEntries.value,
        {
          key,
          ppId: sprName,
          jobId,
          job_id: jobId,
          orderCode: orderCode || sprName,
          lineId: `trial-${jobId}`,
          quality: "",
          color: "",
          gsm: 0,
          plannedDate: runDate.value,
          is_trial: true,
        },
      ];
    }
  }
  scheduleJobSelectionSave();
  await loadJobBoard();
  enrichSelectedEntriesFromBoard();
  await fetchOrders();
  await refreshSessionFromServer({ quiet: true, merge: true });
}

async function runTool(kind) {
  closeToolsMenu();
  if (kind === "trail") {
    if (!shiftOpened.value) {
      frappe.msgprint(__("Open the shift session first."));
      return;
    }
    const unit = headerUnit.value || filterUnit.value;
    if (!unit) {
      frappe.msgprint(__("Unit is required for Trail Order."));
      return;
    }
    await gsmOpenTrailOrder({
      unit,
      runDate: runDate.value,
      shift: shift.value,
      operator: operator.value,
      supervisor: supervisor.value,
      coordinator: coordinator.value,
      onSuccess: attachTrialSprToSession,
    });
    return;
  }
  if (kind === "lamFabricIn" || kind === "lamBoppIn" || kind === "lamOutput") {
    await runLaminationTool(kind);
    return;
  }
  const ctx = await resolveToolContext();
  if (!ctx) {
    return;
  }
  const { ppId, planningNames } = ctx;
  const onSuccess = () => fetchOrders();
  if (kind === "manual") {
    await gsmOpenManualJob(ppId, planningNames, headerUnit.value, runDate.value, shift.value, onSuccess);
  } else if (kind === "bundle") {
    await gsmOpenBundlePackaging(ppId, async (m) => {
      await handleBundleApplyResult(m, ppId);
      await fetchOrders();
    });
  } else if (kind === "bundlese") {
    await gsmToggleBundleSeOnSubmit(ppId);
  } else if (kind === "rmbatches") {
    await gsmOpenRmBatches(ppId);
  } else if (kind === "fixshaft") {
    await gsmBackfillShaftNumbers(ppId);
    await refreshSessionFromServer({ quiet: true, merge: true });
  }
}

async function resolveLaminationSprTarget() {
  const opts = (selectedSessionSprList.value || []).filter((s) => s?.spr_name);
  const all = opts.length ? opts : (shiftSessionSprList.value || []).filter((s) => s?.spr_name);
  if (!all.length) {
    frappe.msgprint(__("Confirm order selection and Create SPRs first."));
    return null;
  }
  if (all.length === 1) {
    return all[0];
  }
  return (await pickToolOrder(all.map((s) => ({
    ppId: s.pp_id,
    orderCode: s.order_code || s.pp_id,
    spr_name: s.spr_name,
  })))) || null;
}

async function runLaminationTool(kind) {
  if (!isLaminationMode.value) {
    frappe.msgprint(__("Switch Unit to Lamination first."));
    return;
  }
  const target = await resolveLaminationSprTarget();
  if (!target?.spr_name) {
    return;
  }
  const sprName = target.spr_name;
  const jobId = selectedEntries.value.find((e) => e.ppId === target.ppId)?.jobId
    || selectedEntries.value.find((e) => e.ppId === target.ppId)?.job_id
    || "";

  if (kind === "lamOutput") {
    const n = await new Promise((resolve) => {
      frappe.prompt(
        [
          {
            fieldname: "roll_lines_to_add",
            fieldtype: "Int",
            label: __("Roll lines to add"),
            reqd: 1,
            default: 1,
            description: __("Adds exactly this many new roll lines for the selected job."),
          },
        ],
        (v) => resolve(cint(v.roll_lines_to_add)),
        __("Lamination — add roll lines"),
        __("Add")
      );
    });
    if (!n || n < 1) {
      return;
    }
    frappe.dom.freeze(__("Adding output rolls…"));
    try {
      const r = await frappe.call({
        method:
          "production_entry.production_planning.unified_production_entry_api.gsm_lamination_add_output_rolls",
        args: {
          spr_name: sprName,
          job_id: jobId || undefined,
          exact_roll_lines: n,
        },
      });
      frappe.show_alert({
        message: __("Added {0} roll line(s)", [cint(r.message?.added || 0)]),
        indicator: "green",
      });
      await refreshSessionFromServer({ quiet: true, merge: true });
      await fetchOrders();
    } catch (e) {
      console.error(e);
      frappe.msgprint(__("Could not add output rolls."));
    } finally {
      frappe.dom.unfreeze();
    }
    return;
  }

  const inputKind = kind === "lamBoppIn" ? "bopp" : "fabric";
  const n = await new Promise((resolve) => {
    frappe.prompt(
      [
        {
          fieldname: "num_rolls",
          fieldtype: "Int",
          label: __("How many {0} input rolls?", [inputKind === "bopp" ? "BOPP" : "fabric"]),
          reqd: 1,
          default: 1,
        },
      ],
      (v) => resolve(cint(v.num_rolls)),
      __("Lamination {0} inputs", [inputKind === "bopp" ? "BOPP" : "fabric"]),
      __("Continue")
    );
  });
  if (!n || n < 1) {
    return;
  }
  frappe.dom.freeze(__("Preparing input rows…"));
  let prep;
  try {
    const r = await frappe.call({
      method:
        "production_entry.production_planning.unified_production_entry_api.gsm_lamination_prepare_input_rows",
      args: { spr_name: sprName, input_kind: inputKind, num_rolls: n },
    });
    prep = r.message || {};
  } catch (e) {
    console.error(e);
    frappe.dom.unfreeze();
    frappe.msgprint(__("Could not prepare input rows."));
    return;
  }
  frappe.dom.unfreeze();
  if (prep.allowed === false) {
    frappe.msgprint(prep.message || __("This input type is not allowed for this process."));
    return;
  }
  const rows = prep.rows || [];
  if (!rows.length) {
    frappe.msgprint(
      __(
        "No {0} RM options on this SPR yet. Save FG roll lines / ensure WO BOM has batch-tracked {0}, or use Select RM batches.",
        [inputKind]
      )
    );
    return;
  }
  const rmOpts = prep.rm_options || [];
  const fields = [];
  rows.forEach((row, idx) => {
    fields.push({ fieldtype: "Section Break", label: __("Input roll {0}", [idx + 1]) });
    fields.push({
      fieldname: `item_${idx}`,
      fieldtype: "Select",
      label: __("RM Item"),
      options: rmOpts.map((o) => o.item_code).join("\n") || row.item_code,
      default: row.item_code,
      reqd: 1,
    });
    fields.push({
      fieldname: `batch_${idx}`,
      fieldtype: "Data",
      label: __("Batch No"),
      reqd: 1,
    });
    fields.push({
      fieldname: `qty_${idx}`,
      fieldtype: "Float",
      label: __("Qty (Kg)"),
      reqd: 1,
    });
  });
  const d = new frappe.ui.Dialog({
    title: __("Enter {0} input rolls ({1})", [inputKind === "bopp" ? "BOPP" : "Fabric", n]),
    fields,
    primary_action_label: __("Save inputs"),
    primary_action(values) {
      const picks = [];
      rows.forEach((row, idx) => {
        const ic = values[`item_${idx}`] || row.item_code;
        const opt = rmOpts.find((o) => o.item_code === ic) || row;
        picks.push({
          work_order: opt.work_order || row.work_order,
          item_code: ic,
          batch_no: values[`batch_${idx}`],
          qty: flt(values[`qty_${idx}`]),
        });
      });
      d.hide();
      frappe.dom.freeze(__("Saving inputs…"));
      frappe
        .call({
          method:
            "production_entry.production_planning.unified_production_entry_api.gsm_lamination_save_input_picks",
          args: { spr_name: sprName, picks_json: JSON.stringify(picks), merge_with_other_kind: 1 },
        })
        .then((r) => {
          frappe.show_alert({
            message: __("Saved {0} input pick(s)", [cint(r.message?.count || picks.length)]),
            indicator: "green",
          });
        })
        .catch((e) => {
          console.error(e);
          frappe.msgprint(__("Could not save input picks."));
        })
        .finally(() => frappe.dom.unfreeze());
    },
  });
  d.show();
}

async function resolveQualityCheckTarget() {
  const selectedOpts = toolsPpOptions.value || [];
  const sessionOpts = (sessionSprList.value || [])
    .filter((s) => s && s.spr_name)
    .map((s) => ({
      ppId: s.pp_id,
      orderCode: s.order_code || s.pp_id,
      spr_name: s.spr_name,
    }));
  const options = selectedOpts.length ? selectedOpts : sessionOpts;
  if (!options.length) {
    frappe.msgprint(
      __("No Shaft Production Run in this session. Confirm jobs and create SPRs first.")
    );
    return null;
  }
  if (options.length === 1) {
    return options[0];
  }
  return (await pickToolOrder(options)) || null;
}

async function runQualityCheck(kind) {
  closeQualityMenu();
  try {
    const target = await resolveQualityCheckTarget();
    if (!target?.spr_name) {
      return;
    }
    const jobId = await promptQualityCheckJobId(target.ppId);
    await gsmOpenQualityCheck({
      sprName: target.spr_name,
      ppId: target.ppId,
      kind,
      jobId,
      session: {
        unit: headerUnit.value || filterUnit.value,
        runDate: runDate.value,
        shift: shift.value,
      },
    });
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not open Quality Checking."));
  }
}

function viewPP(ppId) {
  if (ppId) {
    openProductionPlanPrintPreview(ppId);
  }
}

function openSpr(name) {
  openSprForm(name);
}

function openWorkOrder(wo) {
  if (wo) {
    frappe.set_route("Form", "Work Order", wo);
  }
}

function rowBandClass(row) {
  const hasWeight = sprNormalizeGrossWeightInput(row.gross_weight) > 0;
  return sprGsmBandClass(row.gsm, row.produced_gsm, hasWeight);
}

function applyRollRowRecalc(row) {
  const updated = sprRecalcRollRow({ ...row, core_width_options: coreWidthOptions.value });
  row.net_weight = updated.net_weight;
  row.produced_gsm = updated.produced_gsm;
  if (!row.is_mix_roll_row) {
    row.planned_qty = updated.planned_qty;
  } else {
    row.planned_qty = 0;
  }
  row.custom_cbm_cubic_meters = sprCalcCbmFromDiameter(row.width_inch, row.custom_diameter_inches);
}

function onDiameterInput(row) {
  if (row.row_locked) {
    return;
  }
  row.custom_cbm_cubic_meters = sprCalcCbmFromDiameter(row.width_inch, row.custom_diameter_inches);
  if (row.is_mix_roll_row) {
    syncMixRollRowViews(row);
  } else {
    applyRollRowRecalc(row);
  }
  scheduleAutosave();
}

function onGrossWeightInput(row, event) {
  if (row.row_locked) {
    return;
  }
  row.gross_weight = sprSanitizeGrossWeightTyping(event?.target?.value);
  if (row.is_mix_roll_row) {
    recalcMixRollRow(row);
    syncMixRollRowViews(row);
  } else {
    applyRollRowRecalc(row);
  }
  scheduleAutosave();
}

function onRowEdit(row) {
  if (row.row_locked) {
    return;
  }
  coerceProducedLengthMtrs(row);
  if (row.is_mix_roll_row) {
    recalcMixRollRow(row);
    syncMixRollRowViews(row);
  } else {
    applyRollRowRecalc(row);
  }
  scheduleAutosave();
}

function buildSessionEntries() {
  return selectedEntries.value.map((entry) => ({
    pp_id: entry.ppId,
    job_id: entry.jobId || entry.job_id,
    jobId: entry.jobId || entry.job_id,
    lineId: entry.lineId,
    itemName: entry.lineId,
    planning_table_row: entry.lineId,
    orderCode: entry.orderCode,
    quality: entry.quality,
    color: entry.color,
    gsm: entry.gsm,
    width_inch: entry.width_inch,
    plannedDate: entry.plannedDate,
  }));
}

function entriesForPp(ppId) {
  const fromSel = buildSessionEntries().filter((e) => (e.ppId || e.pp_id) === ppId);
  if (fromSel.length) {
    return fromSel;
  }
  const roll = rollLines.value.find((r) => r.pp_id === ppId && !r.is_mix_roll_row);
  if (!ppId) {
    return [];
  }
  return [
    {
      pp_id: ppId,
      job_id: roll?.job_id || roll?.job || "",
      jobId: roll?.job_id || roll?.job || "",
      lineId: roll?.planning_table_row || roll?.job_id || "gsm-job",
      orderCode: roll?.party_code || "",
    },
  ];
}

function sprNameForPp(ppId) {
  return draftSprNameForPp(ppId);
}

function markSessionSprsSubmitted(submittedRows) {
  const next = { ...sessionSprs.value };
  for (const row of submittedRows || []) {
    const sprName = row.spr_name;
    if (!sprName) {
      continue;
    }
    let ppId = row.pp_id;
    if (!ppId) {
      for (const [k, v] of Object.entries(next)) {
        if (v?.spr_name === sprName) {
          ppId = k;
          break;
        }
      }
    }
    if (!ppId) {
      continue;
    }
    next[ppId] = {
      ...(next[ppId] || { pp_id: ppId }),
      spr_name: sprName,
      submitted: true,
      docstatus: 1,
    };
    forceNewSprByPp.value = { ...forceNewSprByPp.value, [ppId]: false };
  }
  sessionSprs.value = next;
  scheduleAutosave();
}

function widthDisplay(row) {
  if (!row) {
    return "";
  }
  if (row.width_label) {
    return row.width_label;
  }
  if (row.is_bundle_row && row.pack_count > 1 && row.segment_width) {
    const w = row.segment_width;
    const lbl = Number.isInteger(w) ? String(w) : String(w);
    return `${lbl}" (${row.pack_count} rolls)`;
  }
  return row.width_inch != null && row.width_inch !== "" ? row.width_inch : "";
}

async function handleBundleApplyResult(m, ppId) {
  if (!m || m.status !== "ok") {
    return;
  }
  let coreItem = "";
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_roll_row_extras",
      args: {
        gsm: m.gsm,
        width_inch: m.segment_width || 0,
        length_m: m.produced_length_mtrs || m.meter_roll || 0,
        pp_id: m.pp_id || ppId,
        job_id: m.job_id || "",
      },
    });
    const extras = res.message || {};
    coreItem = pickCoreForFabricWidth(
      m.segment_width || 0,
      extras.custom_core_width_mm || extras.core_size || ""
    );
  } catch (e) {
    coreItem = pickCoreForFabricWidth(m.segment_width || 0, "");
  }
  const bundleSeq = nextCreationSeq();
  const bundleRow = {
    _id: `bundle-${Date.now()}-${bundleSeq}`,
    creation_seq: bundleSeq,
    is_bundle_row: true,
    pp_id: m.pp_id || ppId,
    party_code: m.order_code || "",
    job_id: m.job_id || "",
    quality: m.quality || "",
    color: m.color || "",
    gsm: m.gsm || "",
    width_inch: m.segment_width || 0,
    width_label: m.width_label || "",
    pack_count: m.pack_count || 0,
    segment_width: m.segment_width || 0,
    batch_no: m.bundle_batch_no || "",
    roll_no: "",
    roll_numbers: m.roll_numbers || "",
    combination: m.combination || "",
    meter_roll: m.meter_roll || 0,
    produced_length_mtrs: sprWholeMtrs(m.produced_length_mtrs) || 0,
    produced_gsm: 0,
    net_weight: m.sticker_bundle_weight_kg || 0,
    gross_weight: m.whole_gross_kg != null && m.whole_gross_kg !== "" ? String(m.whole_gross_kg) : "",
    planned_qty: m.planned_qty || 0,
    uom: m.uom || "Kg",
    work_order: m.work_order || "",
    child_roll_batches: m.child_roll_batches || [],
    child_spr_item_names: m.child_spr_item_names || [],
    spr_item_name: "",
    row_locked: 1,
    row_ready_for_print: 1,
    custom_core_width_mm: coreItem,
    core_width_options: coreWidthOptions.value,
  };
  const bundleRecalc = sprRecalcRollRow(bundleRow);
  bundleRow.net_weight = bundleRecalc.net_weight;
  bundleRow.produced_gsm = bundleRecalc.produced_gsm;
  bundleRow.planned_qty = bundleRecalc.planned_qty;
  rollLines.value = sortRollLinesLifo([bundleRow, ...rollLines.value]);
  if (m.child_roll_batches?.length) {
    syncBatchCounterFromGrid();
  }
  saveStatus.value = __("Bundle applied — {0} roll(s)", [m.updated_rolls || m.pack_count || 0]);
  scheduleAutosave();
  await loadJobBoard();
  frappe.show_alert({
    message: __("Bundle row added ({0})", [m.width_label || m.bundle_batch_no]),
    indicator: "green",
  });
}

async function fetchShaftJobsForPp(ppId) {
  if (!ppId) {
    return [];
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_pp_shaft_details",
      args: { pp_ids: JSON.stringify([ppId]) },
    });
    const rows = res.message || [];
    const hit = rows.find((r) => r.pp_id === ppId);
    return hit?.shaft_rows || [];
  } catch (e) {
    return [];
  }
}

function pickJobIdForLine(line) {
  const ppId = line?.ppId;
  if (!ppId) {
    return Promise.resolve("");
  }
  return fetchShaftJobsForPp(ppId).then((jobs) => {
    const ids = [
      ...new Set(
        (jobs || [])
          .map((j) => String(j.job || "").trim())
          .filter(Boolean)
      ),
    ];
    if (ids.length <= 1) {
      return ids[0] || "";
    }
    return new Promise((resolve) => {
      const d = new frappe.ui.Dialog({
        title: __("Select Job ID"),
        fields: [
          {
            fieldname: "job_id",
            fieldtype: "Select",
            label: __("Job ID"),
            options: ids.join("\n"),
            reqd: 1,
            default: ids[0],
          },
        ],
        primary_action_label: __("Continue"),
        primary_action(values) {
          d.hide();
          resolve(values.job_id || ids[0]);
        },
      });
      d.show();
    });
  });
}

function buildRollPayload(row) {
  const meta = orderMetaForPp(row.pp_id);
  return {
    pp_id: row.pp_id,
    planning_table_row: row.planning_table_row,
    party_code: row.party_code,
    item_code: row.item_code,
    item_name: row.item_name,
    quality: row.quality || meta.quality || "",
    color: row.color || row.fabric_colour || meta.color || "",
    gsm: row.gsm,
    batch_no: row.batch_no,
    roll_no: row.roll_no,
    width_inch: row.width_inch,
    meter_roll: row.meter_roll,
    produced_length_mtrs: sprWholeMtrs(row.produced_length_mtrs) || 0,
    produced_gsm: row.produced_gsm,
    net_weight: row.net_weight,
    gross_weight: sprNormalizeGrossWeightInput(row.gross_weight),
    planned_qty: row.planned_qty,
    work_order: row.work_order,
    uom: row.uom || "Kg",
    custom_core_width_mm: row.custom_core_width_mm,
    custom_polybag_kgs: row.custom_polybag_kgs,
    custom_diameter_inches: row.custom_diameter_inches,
    custom_diameter: sprFlt(row.custom_diameter_inches),
    custom_cbm_cubic_meters: row.custom_cbm_cubic_meters,
    custom_cbm: sprFlt(row.custom_cbm_cubic_meters),
    custom_bay: row.custom_bay || "",
    custom_unit: headerUnit.value || row.custom_unit || "",
    run_date: runDate.value || "",
    job_id: row.job_id || row.job || "",
    custom_no_of_shaft: resolveRowShaftNo(row),
    is_bundle_row: row.is_bundle_row ? 1 : 0,
    row_locked: row.row_locked ? 1 : 0,
    row_ready_for_print: row.row_ready_for_print ? 1 : 0,
  };
}

function buildSessionSprsPayload() {
  const fabric = submitSprList.value.map((s) => ({
    pp_id: s.pp_id,
    spr_name: s.spr_name,
    order_code: s.order_code,
  }));
  const mix = mixSubmitSprList.value.map((s) => ({
    pp_id: "",
    spr_name: s.spr_name,
    order_code: s.order_code,
    is_mix_roll: 1,
  }));
  return [...fabric, ...mix];
}

function buildSubmitRollsPayload() {
  const submitPpIds = new Set(submitSprList.value.map((s) => s.pp_id));
  return rollLines.value
    .filter(
      (r) =>
        !r.is_bundle_row &&
        !r.is_wasted &&
        r.row_locked &&
        r.batch_no &&
        submitPpIds.has(r.pp_id)
    )
    .map(buildRollPayload);
}

async function clearGridEntries() {
  if (!rollLines.value.length && !sessionSprList.value.length && !activeMixRoll.value && !mixRollLines.value.length) {
    return;
  }
  frappe.confirm(
    __("Clear all roll rows and mix roll workspace from this screen? Server SPRs are kept — you can add new rows without creating SPRs again."),
    () => {
      rollLines.value = [];
      mixRollLines.value = [];
      activeMixRoll.value = null;
      persistedActiveMixSpr.value = "";
      sessionJobApiBaseline.value = {};
      forceNewSprSession.value = false;
      resetBatchSeriesCache();
      if (shiftBatchPrefix.value) {
        seriesPrefix.value = shiftBatchPrefix.value;
      }
      saveStatus.value = "Cleared — add new roll rows";
      scheduleAutosave();
      frappe.show_alert({ message: __("Grid and mix roll cleared"), indicator: "blue" });
    }
  );
}

async function createSprs(options = {}) {
  const ppIds = Array.isArray(options?.ppIds) ? options.ppIds.filter(Boolean) : [];
  if (!options?.silent && !ppIds.length && !canCreateSprs.value) {
    return false;
  }
  // Ensure Planning Table ids are filled before API call (lamination autosave may lack them)
  try {
    enrichSelectedEntriesFromBoard();
  } catch (e) {
    /* ignore */
  }
  for (let i = 0; i < selectedEntries.value.length; i++) {
    const e = selectedEntries.value[i];
    if (!e?.ppId) continue;
    const bad =
      !e.lineId ||
      String(e.lineId).startsWith("job-") ||
      e.lineId === "1" ||
      e.lineId === "gsm-job";
    if (!bad) continue;
    const meta = orderMetaForPp(e.ppId);
    if (meta.planningLineId) {
      selectedEntries.value[i] = { ...e, lineId: meta.planningLineId };
    }
  }
  const allEntries = buildSessionEntries();
  let entries = allEntries;
  if (ppIds.length) {
    const wanted = new Set(ppIds);
    entries = allEntries.filter((e) => wanted.has(e.ppId || e.pp_id));
    for (const ppId of ppIds) {
      if (!entries.some((e) => (e.ppId || e.pp_id) === ppId)) {
        entries = entries.concat(entriesForPp(ppId));
      }
    }
  } else {
    const missing = [
      ...new Set(allEntries.map((e) => e.ppId || e.pp_id).filter((id) => ppNeedsNewSpr(id))),
    ];
    if (missing.length) {
      entries = allEntries.filter((e) => missing.includes(e.ppId || e.pp_id));
    }
  }
  if (!entries.length) {
    if (!options?.silent) {
      frappe.msgprint(__("Select at least one line."));
    }
    return false;
  }
  const forceNew =
    !!forceNewSprSession.value ||
    entries.some((e) => !!forceNewSprByPp.value[e.ppId || e.pp_id]);
  saveStatus.value = "Creating SPRs…";
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.create_gsm_sprs_for_session",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
        operator: operator.value,
        supervisor: supervisor.value,
        coordinator: coordinator.value,
        entries: JSON.stringify(entries),
        force_new_session: forceNew ? 1 : 0,
      },
    });
    const sprs = (res.message || {}).sprs || [];
    const next = { ...sessionSprs.value };
    const errors = [];
    for (const row of sprs) {
      if (row.status === "ok" && row.spr_name) {
        next[row.pp_id] = {
          pp_id: row.pp_id,
          spr_name: row.spr_name,
          order_code: row.order_code,
          label_type: row.label_type,
          reused: row.reused,
          submitted: false,
          docstatus: 0,
        };
      } else {
        errors.push(row.message || row.pp_id);
      }
    }
    sessionSprs.value = next;
    for (const row of sprs) {
      if (row.status === "ok" && row.pp_id) {
        const cleared = { ...forceNewSprByPp.value };
        delete cleared[row.pp_id];
        forceNewSprByPp.value = cleared;
      }
    }
    forceNewSprSession.value = false;
    recordJobApiBaselines(jobBoardJobs.value);
    scheduleAutosave();
    scheduleJobSelectionSave();
    if (errors.length && !options?.silent) {
      frappe.msgprint({
        title: __("Some SPRs failed"),
        message: errors.join("<br>"),
        indicator: "orange",
      });
    }
    const okCount = sprs.filter((s) => s.status === "ok").length;
    if (okCount && !options?.silent) {
      frappe.show_alert({ message: __("{0} SPR(s) ready", [okCount]), indicator: "green" });
    }
    saveStatus.value = "SPRs created";
    return okCount > 0;
  } catch (e) {
    console.error(e);
    saveStatus.value = "SPR create failed";
    if (!options?.silent) {
      frappe.msgprint(__("Could not create SPRs. Check console."));
    }
    return false;
  }
}

async function ensureSprForPp(ppId) {
  if (!ppId) {
    return "";
  }
  let sprName = sprNameForPp(ppId);
  if (sprName) {
    return sprName;
  }
  await createSprs({ ppIds: [ppId], silent: true });
  return sprNameForPp(ppId);
}

function openToleranceDialog(orders) {
  toleranceOrders.value = orders || [];
  const form = {};
  for (const o of toleranceOrders.value) {
    form[o.spr_name] = { reason: "", approved: false };
  }
  toleranceForm.value = form;
  showToleranceDialog.value = true;
}

async function openSubmitConfirmDialog() {
  // Auto Save Row any complete lines that still lack spr_item_name / lock.
  const unsaved = submitConfirmRolls.value.filter((r) => !(r.row_locked || r.spr_item_name));
  const failed = [];
  for (const row of unsaved) {
    try {
      await saveRow(row);
      if (!(row.row_locked || row.spr_item_name)) {
        failed.push(row.batch_no || row._id);
      }
    } catch (e) {
      console.error(e);
      failed.push(row.batch_no || row._id);
    }
  }
  if (!canSubmitEntry.value || failed.length || !allSubmitRollsSaved.value) {
    const hasMix = submitConfirmRolls.value.some((r) => r.is_mix_roll_row);
    if (failed.length) {
      frappe.msgprint(
        __(
          "Cannot submit until every roll is Save Row'd to its SPR. Still unsaved: {0}",
          [failed.join(", ")]
        )
      );
    } else if (hasMix) {
      frappe.msgprint(__("Save each mix roll row, then submit."));
    } else {
      frappe.msgprint(__("Create SPRs, enter rolls, and Save Row on each line before submit."));
    }
    return;
  }
  submitDialogPhase.value = "review";
  submitSuccessResult.value = null;
  submitErrorMessage.value = "";
  showSubmitConfirmDialog.value = true;
}

function stopSubmitProgressTimer() {
  if (submitProgressTimer) {
    clearInterval(submitProgressTimer);
    submitProgressTimer = null;
  }
}

function startSubmitProgressTimer(rollCount) {
  stopSubmitProgressTimer();
  submitProgressStart = Date.now();
  const rc = rollCount || submitConfirmRolls.value.length;
  const updateMsg = () => {
    const sec = Math.round((Date.now() - submitProgressStart) / 1000);
    if (sec >= 60) {
      submitProgressMessage.value = __(
        "Submitting {0} rolls — {1}s elapsed. Still working, do not reload.",
        [rc, sec]
      );
    } else if (sec >= 30) {
      submitProgressMessage.value = __("Creating manufacture entries for {0} rolls — {1}s. Do not reload.", [rc, sec]);
    } else if (sec >= 10) {
      submitProgressMessage.value = __("Checking stock and posting manufacture entries — please wait...");
    } else {
      submitProgressMessage.value = __("Submitting {0} roll(s) — validating and posting…", [rc]);
    }
  };
  updateMsg();
  submitProgressTimer = setInterval(updateMsg, 2000);
}

function closeSubmitDialog() {
  stopSubmitProgressTimer();
  showSubmitConfirmDialog.value = false;
  submitDialogPhase.value = "review";
  submitSuccessResult.value = null;
  submitErrorMessage.value = "";
}

async function pollGsmSubmitRecovery(sprNames, options = {}) {
  const names = (sprNames || []).filter(Boolean);
  if (!names.length) {
    return false;
  }
  const maxAttempts = cint(options.maxAttempts) || 24;
  const delayMs = cint(options.delayMs) || 5000;
  for (let i = 0; i < maxAttempts; i++) {
    if (i > 0 || options.delayFirst) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    let allSubmitted = true;
    try {
      const headers = await gsmSprHeaders(names);
      for (const name of names) {
        if (cint(headers[name]?.docstatus) !== 1) {
          allSubmitted = false;
          break;
        }
      }
    } catch (e) {
      allSubmitted = false;
    }
    if (allSubmitted) {
      return true;
    }
  }
  return false;
}

function mixSubmitErrorLooksAlreadySubmitted(err) {
  const msg = _cstr(err).toLowerCase();
  return msg.includes("already submitted") || msg.includes("cannot submit");
}

function showSubmitSuccess(submitted, totalKg) {
  const sprNames = (submitted || []).map((s) => s.spr_name).filter(Boolean);
  submitSuccessResult.value = {
    count: sprNames.length,
    sprNames,
    rollCount: submitConfirmRolls.value.length,
    totalKg: totalKg || metrics.value.totalNet,
  };
  submitDialogPhase.value = "success";
  frappe.show_alert({ message: __("Successfully submitted"), indicator: "green" });
}

async function confirmSubmitEntry() {
  submitDialogPhase.value = "submitting";
  startSubmitProgressTimer(submitConfirmRolls.value.length);
  await submitEntry();
}

async function openShiftDialog() {
  if (!headerUnit.value || !runDate.value || !shift.value) {
    frappe.msgprint(__("Select unit, run date, and shift first."));
    return;
  }
  operator.value = "";
  supervisor.value = "";
  coordinator.value = "";
  shiftReopenReason.value = "";
  shiftReopenRemarks.value = "";
  shiftReopenRequired.value = false;
  shiftReopenPreviousSession.value = "";
  shiftReopenClosedBatch.value = "";
  shiftBatchReuseNotice.value = "";
  await previewShiftBatchPrefix();
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.check_gsm_shift_reopen_required",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
      },
    });
    const msg = res.message || {};
    shiftReopenRequired.value = !!msg.required;
    shiftReopenPreviousSession.value = msg.previous_session || "";
    shiftReopenClosedBatch.value = msg.closed_batch || "";
    if (!shiftReopenRequired.value && msg.reused_batch) {
      const fromShift = msg.reused_from_shift || __("prior shift");
      shiftBatchReuseNotice.value = __(
        "Reusing unused batch {0} from {1} — no rolls were entered on that session.",
        [msg.reused_batch, fromShift]
      );
    }
  } catch (e) {
    console.warn("shift reopen check", e);
  }
  showShiftOpenDialog.value = true;
}

function closeShiftDialog() {
  showShiftOpenDialog.value = false;
  shiftReopenReason.value = "";
  shiftReopenRemarks.value = "";
}

async function callSubmitGsm(overrides = []) {
  return gsmSafeCall({
    method: "production_entry.production_planning.unified_production_entry_api.submit_gsm_production_entry",
    type: "POST",
    args: {
      run_date: runDate.value,
      shift: shift.value,
      unit: headerUnit.value,
      operator: operator.value,
      supervisor: supervisor.value,
      coordinator: coordinator.value,
      rolls: JSON.stringify(buildSubmitRollsPayload()),
      session_sprs: JSON.stringify(buildSessionSprsPayload()),
      tolerance_overrides: JSON.stringify(overrides),
    },
  });
}

async function submitEntry(overrides = []) {
  if (!canSubmitEntry.value && !overrides.length) {
    frappe.msgprint(__("Create SPRs, enter rolls, and Save Row on each line before submit."));
    return;
  }
  const unsavedStill = submitConfirmRolls.value.filter((r) => !(r.row_locked || r.spr_item_name));
  if (unsavedStill.length && !overrides.length) {
    frappe.msgprint(
      __(
        "Save Row required for {0} roll(s) before submit: {1}",
        [unsavedStill.length, unsavedStill.map((r) => r.batch_no || r._id).join(", ")]
      )
    );
    return;
  }
  if (!(await ensureGsmDeskSession())) {
    return;
  }
  const fabricRolls = submitConfirmRolls.value.filter((r) => !r.is_mix_roll_row);
  const missingSpr = [...new Set(fabricRolls.map((r) => r.pp_id).filter(Boolean))].filter(
    (pp) => !sprNameForPp(pp)
  );
  if (missingSpr.length) {
    frappe.msgprint(__("Create SPRs for all orders with saved rolls first."));
    return;
  }
  const sprNamesToSubmit = allSubmitSprList.value.map((s) => s.spr_name).filter(Boolean);
  submitInProgress.value = true;
  saveStatus.value = "Submitting…";
  if (!showSubmitConfirmDialog.value) {
    submitDialogPhase.value = "submitting";
    startSubmitProgressTimer(submitConfirmRolls.value.length);
    showSubmitConfirmDialog.value = true;
  }
  try {
    let msg = { status: "ok", submitted: [], failed: [], total_kg: 0 };
    if (submitSprList.value.length) {
      const res = await callSubmitGsm(overrides);
      msg = res.message || msg;
      if (msg.status === "tolerance_required") {
        stopSubmitProgressTimer();
        showSubmitConfirmDialog.value = false;
        openToleranceDialog(msg.orders || []);
        saveStatus.value = "Tolerance approval needed";
        return;
      }
      if (msg.status === "import_failed" || msg.status === "failed") {
        const errs = (msg.failed || []).map((f) => `${f.spr_name || f.pp_id}: ${f.error}`).join("<br>");
        submitErrorMessage.value = errs || __("Unknown error");
        submitDialogPhase.value = "error";
        saveStatus.value = "Submit failed";
        return;
      }
    }

    const mixSubmitted = [];
    const mixFailed = [];
    const mixNames = mixSubmitSprList.value.map((s) => s.spr_name).filter(Boolean);
    for (const mixSpr of mixSubmitSprList.value) {
      try {
        await submitGsmMixRollSpr(mixSpr.spr_name);
        mixSubmitted.push({ spr_name: mixSpr.spr_name, pp_id: "", is_mix_roll: 1 });
      } catch (e) {
        const errMsg = _cstr(e?.message || e);
        if (mixSubmitErrorLooksAlreadySubmitted(errMsg)) {
          mixSubmitted.push({ spr_name: mixSpr.spr_name, pp_id: "", is_mix_roll: 1, recovered: 1 });
        } else {
          mixFailed.push({ spr_name: mixSpr.spr_name, error: errMsg });
        }
      }
    }
    // 502 / gateway: server may have submitted — poll remaining failures.
    if (mixFailed.length) {
      const recoverNames = mixFailed.map((f) => f.spr_name);
      const recovered = await pollGsmSubmitRecovery(recoverNames, { maxAttempts: 12, delayMs: 3000, delayFirst: true });
      if (recovered) {
        for (const name of recoverNames) {
          mixSubmitted.push({ spr_name: name, pp_id: "", is_mix_roll: 1, recovered: 1 });
        }
        mixFailed.length = 0;
      } else {
        // Partial poll: move any that are already submitted out of failed.
        const stillFailed = [];
        for (const f of mixFailed) {
          try {
            const headers = await gsmSprHeaders([f.spr_name]);
            if (cint(headers[f.spr_name]?.docstatus) === 1) {
              mixSubmitted.push({ spr_name: f.spr_name, pp_id: "", is_mix_roll: 1, recovered: 1 });
            } else {
              stillFailed.push(f);
            }
          } catch (e) {
            stillFailed.push(f);
          }
        }
        mixFailed.length = 0;
        mixFailed.push(...stillFailed);
      }
    }
    showToleranceDialog.value = false;
    const submitted = [...(msg.submitted || []), ...mixSubmitted];
    const failed = [...(msg.failed || []), ...mixFailed];
    const partial = failed.length;
    if (partial) {
      frappe.msgprint({
        title: __("Partial submit"),
        message: failed.map((f) => `${f.spr_name}: ${f.error}`).join("<br>"),
        indicator: "orange",
      });
    }
    if (submitted.length) {
      markSessionSprsSubmitted(submitted);
      showSubmitSuccess(submitted, msg.total_kg);
      saveStatus.value = "Submitted";
      const submittedMixNames = new Set(mixSubmitted.map((s) => s.spr_name));
      if (activeMixRoll.value?.spr_name && submittedMixNames.has(activeMixRoll.value.spr_name)) {
        clearActiveMixRoll();
      }
      // Drop mix rows for submitted mix SPRs from the main grid.
      if (submittedMixNames.size) {
        rollLines.value = rollLines.value.filter(
          (r) => !(r.is_mix_roll_row && submittedMixNames.has(r.spr_name))
        );
        mixRollLines.value = [];
      }
      await loadMixRollCandidates();
      await fetchOrders();
      return;
    }
    saveStatus.value = "Submit failed";
    submitErrorMessage.value = __("No SPRs were submitted.");
    submitDialogPhase.value = "error";
  } catch (e) {
    if (e?.message === "session expired" || isGsmSessionDeadError(e)) {
      markGsmSessionExpired();
      showSubmitConfirmDialog.value = false;
      saveStatus.value = "Login expired";
      return;
    }
    console.error(e);
    if (recovered) {
      markSessionSprsSubmitted(sprNamesToSubmit.map((sn) => ({ spr_name: sn })));
      showSubmitSuccess(
        sprNamesToSubmit.map((sn) => ({ spr_name: sn })),
        metrics.value.totalNet
      );
      saveStatus.value = "Submitted";
      clearActiveMixRoll();
      await loadMixRollCandidates();
      await fetchOrders();
      return;
    }
    saveStatus.value = "Submit failed";
    submitErrorMessage.value = __("Submit failed. The server may still be processing — refresh and check SPR status.");
    submitDialogPhase.value = "error";
    frappe.msgprint(__("Submit failed. Check console or refresh SPR status."));
  } finally {
    stopSubmitProgressTimer();
    submitInProgress.value = false;
  }
}

function submitWithTolerance() {
  if (!toleranceFormComplete.value) {
    frappe.msgprint(__("Enter reason and approve for every order."));
    return;
  }
  const overrides = toleranceOrders.value.map((o) => ({
    spr_name: o.spr_name,
    reason: (toleranceForm.value[o.spr_name]?.reason || "").trim(),
    approved: toleranceForm.value[o.spr_name]?.approved ? 1 : 0,
  }));
  showToleranceDialog.value = false;
  submitDialogPhase.value = "submitting";
  showSubmitConfirmDialog.value = true;
  startSubmitProgressTimer(submitConfirmRolls.value.length);
  submitEntry(overrides);
}

async function saveRow(row) {
  if (row?.is_wasted || row?.row_readonly) {
    frappe.msgprint(__("Wasted rolls are read-only."));
    return;
  }
  if (row?.is_mix_roll_row) {
    await saveMixRollRow(row);
    return;
  }
  if (!row?.pp_id) {
    frappe.msgprint(__("Roll is missing production plan — re-add the row."));
    return;
  }
  let sprName = sprNameForPp(row.pp_id);
  if (!sprName) {
    saveStatus.value = "Creating SPR for this order…";
    sprName = await ensureSprForPp(row.pp_id);
  }
  if (!sprName) {
    frappe.msgprint(__("This order has no SPR yet. Click Create SPR for new order, then Save Row."));
    return;
  }
  const mapped = sessionSprs.value[row.pp_id];
  if (!mapped?.spr_name || mapped.spr_name !== sprName) {
    frappe.msgprint(__("Roll order does not match shift SPR mapping. Create or refresh SPRs."));
    return;
  }
  if (!row.batch_no) {
    frappe.msgprint(__("Batch number is required."));
    return;
  }
  const gross = sprNormalizeGrossWeightInput(row.gross_weight);
  if (gross <= 0) {
    frappe.msgprint(__("Enter gross weight before saving."));
    return;
  }
  const updated = sprRecalcRollRow({ ...row, core_width_options: coreWidthOptions.value });
  row.net_weight = updated.net_weight;
  row.produced_gsm = updated.produced_gsm;
  row.planned_qty = updated.planned_qty;
  row.gross_weight = String(gross);
  saveStatus.value = "Saving row…";
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.save_gsm_roll_line",
      args: {
        spr_name: sprName,
        shift: shift.value,
        roll_payload: JSON.stringify(buildRollPayload({ ...row, row_locked: 1, row_ready_for_print: 1 })),
      },
    });
    const msg = res.message || {};
    row.row_locked = 1;
    row.row_ready_for_print = 1;
    if (msg.row_name) {
      row.spr_item_name = msg.row_name;
    }
    const saved = msg.roll_line || {};
    if (saved.net_weight != null) {
      row.net_weight = saved.net_weight;
    }
    if (saved.produced_gsm != null) {
      row.produced_gsm = saved.produced_gsm;
    }
    if (saved.gross_weight != null && saved.gross_weight !== "") {
      row.gross_weight = String(saved.gross_weight);
    }
    if (saved.custom_core_width_mm != null) {
      row.custom_core_width_mm = saved.custom_core_width_mm;
    }
    if (saved.custom_polybag_kgs != null) {
      row.custom_polybag_kgs = saved.custom_polybag_kgs;
    }
    if (saved.custom_no_of_shaft != null && cint(saved.custom_no_of_shaft) > 0) {
      row.custom_no_of_shaft = cint(saved.custom_no_of_shaft);
    }
    if (saved.custom_diameter_inches != null || saved.custom_diameter != null) {
      const dia = normalizeSpiDiameterCbm(saved);
      row.custom_diameter_inches = dia.custom_diameter_inches;
      row.custom_cbm_cubic_meters = dia.custom_cbm_cubic_meters || row.custom_cbm_cubic_meters;
    } else if (saved.custom_cbm != null || saved.custom_cbm_cubic_meters != null) {
      const dia = normalizeSpiDiameterCbm(saved);
      row.custom_cbm_cubic_meters = dia.custom_cbm_cubic_meters;
    }
    if (saved.custom_bay != null) {
      row.custom_bay = saved.custom_bay || "";
    }
    if (row.is_mix_roll_row) {
      row.planned_qty = 0;
      syncMixRollRowViews(row);
    }
    scheduleAutosave();
    saveStatus.value = "Saved to SPR";
    frappe.show_alert({ message: __("Row saved to {0}", [sprName]), indicator: "green" });
    await loadJobBoard();
  } catch (e) {
    console.error(e);
    saveStatus.value = "Save failed";
    frappe.msgprint(__("Could not save row to server."));
  }
}

function editRow(row) {
  row.row_locked = 0;
  row.row_ready_for_print = 0;
  syncMixRollRowViews(row);
  scheduleAutosave();
}

function isRowLabelReady(row) {
  return !!(row.row_locked || row.row_ready_for_print || row.spr_item_name);
}

async function resolveSprItemRowName(row) {
  if (row.spr_item_name) {
    return row.spr_item_name;
  }
  const sprName = sprNameForRow(row);
  if (!sprName || !row.batch_no) {
    return "";
  }
  try {
    const doc = await (production_entry.spr_label?.load_spr_doc
      ? production_entry.spr_label.load_spr_doc(sprName)
      : frappe
          .call({
            method: "production_entry.production_planning.unified_production_entry_api.get_gsm_spr_doc",
            args: { spr_name: sprName },
          })
          .then((r) => r.message));
    const batch = String(row.batch_no || "").trim();
    const hit = (doc?.items || []).find((it) => String(it.batch_no || "").trim() === batch);
    const name = hit?.name || "";
    if (name) {
      row.spr_item_name = name;
      row.row_locked = 1;
      row.row_ready_for_print = 1;
      scheduleAutosave();
    }
    return name;
  } catch (e) {
    return "";
  }
}

function sprNameForRow(row) {
  return row.is_mix_roll_row
    ? row.spr_name || activeMixRoll.value?.spr_name
    : sprNameForPp(row.pp_id);
}

async function printLabel(row) {
  if (!isRowLabelReady(row)) {
    frappe.msgprint(__("Save Row first to enable the label."));
    return;
  }
  const sprName = sprNameForRow(row);
  if (!sprName) {
    frappe.msgprint(__("Create SPRs first."));
    return;
  }
  if (row.is_bundle_row) {
    try {
      await gsmPrintBundleLabel(sprName, row);
    } catch (e) {
      console.error(e);
      frappe.msgprint(__("Could not open bundle label print."));
    }
    return;
  }
  let itemName = row.spr_item_name || (await resolveSprItemRowName(row));
  if (!itemName) {
    frappe.msgprint(__("Save Row first to enable the label."));
    return;
  }
  try {
    await gsmPrintRollLabel(sprName, itemName, row);
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not open label print."));
  }
}

async function printQcLabel(row) {
  if (!isRowLabelReady(row)) {
    frappe.msgprint(__("Save Row first to enable the QC label."));
    return;
  }
  const sprName = sprNameForRow(row);
  if (!sprName) {
    frappe.msgprint(__("Create SPRs first."));
    return;
  }
  let itemName = row.spr_item_name || (await resolveSprItemRowName(row));
  if (!itemName && !row.batch_no) {
    frappe.msgprint(__("Save Row first to enable the QC label."));
    return;
  }
  try {
    await gsmPrintQcLabel(sprName, itemName, row, {
      operator: operator.value,
      supervisor: supervisor.value,
      coordinator: coordinator.value,
    });
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not open QC label print."));
  }
}

function buildFetchArgs() {
  const lam = isLaminationMode.value;
  const scope = String(props.processScope || "").trim();
  const args = {
    board_slug: BOARD_SLUG.value,
    plan_name: "__all__",
    planned_only: 1,
    board_process_scope: scope || (lam ? "lamination_only" : "only_100"),
  };
  if (lam || scope === "lamination_only") {
    args.lamination_process = ""; // both 104 + 107 via chart when empty — API may need one; fetch both client-side if needed
  }
  if (viewScope.value === "monthly" && filterMonth.value) {
    const [year, month] = filterMonth.value.split("-");
    const lastDay = new Date(parseInt(year, 10), parseInt(month, 10), 0).getDate();
    args.start_date = `${filterMonth.value}-01`;
    args.end_date = `${filterMonth.value}-${String(lastDay).padStart(2, "0")}`;
  } else if (viewScope.value === "weekly" && filterWeek.value) {
    const [yearStr, weekStr] = filterWeek.value.split("-W");
    const y = parseInt(yearStr, 10);
    const w = parseInt(weekStr, 10);
    const simple = new Date(y, 0, 1 + (w - 1) * 7);
    const dow = simple.getDay();
    const start = new Date(simple);
    if (dow <= 4) {
      start.setDate(simple.getDate() - simple.getDay() + 1);
    } else {
      start.setDate(simple.getDate() + 8 - simple.getDay());
    }
    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    const fmt = (d) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    args.start_date = fmt(start);
    args.end_date = fmt(end);
  } else {
    args.date = ordersBrowseDate();
  }
  return args;
}

function mergeFetchDate() {
  if (viewScope.value === "daily") {
    return filterDate.value;
  }
  const args = buildFetchArgs();
  return args.date || args.start_date || filterDate.value;
}

async function fetchMerges() {
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_merges_for_date",
      args: {
        date: mergeFetchDate(),
        unit: filterUnit.value || null,
        plan_name: "Default",
      },
    });
    merges.value = Array.isArray(res.message) ? res.message : [];
  } catch (e) {
    console.warn("merge fetch failed", e);
    merges.value = [];
  }
}

async function loadQuotaForLines() {
  const cache = {};
  const seen = new Set();
  const rows = filteredPpSubmittedRows.value;
  const quotaArgs = quotaContextArgs();
  const tasks = rows.map(async (item) => {
    const lineId = item.itemName || item.name;
    const key = `${item.pp_id}::${lineId}::${item.gsm}::${item.width_inch}::${quotaArgs.run_date}::${quotaArgs.shift}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    try {
      const res = await frappe.call({
        method: "production_entry.production_planning.unified_production_entry_api.get_pt_line_roll_quota_status",
        args: {
          pp_id: item.pp_id,
          production_plan_item: lineId,
          gsm: item.gsm,
          width_inch: sprFlt(item.width_inch || item.width),
          item_code: item.itemCode || item.item_code,
          run_date: quotaArgs.run_date,
          shift: quotaArgs.shift,
          unit: quotaArgs.unit,
        },
      });
      cache[lineId] = res.message || {};
    } catch (e) {
      console.warn("quota fetch", lineId, e);
    }
  });
  await Promise.all(tasks);
  quotaByLineId.value = cache;
}

async function loadJobBoard() {
  const ppIds = new Set([...sidebarAllowedPpIds.value]);
  if (shiftOpened.value) {
    for (const entry of selectedEntries.value) {
      if (entry.ppId) {
        ppIds.add(entry.ppId);
      }
    }
    for (const row of rollLines.value) {
      if (row.pp_id) {
        ppIds.add(row.pp_id);
      }
    }
  }
  if (!ppIds.size) {
    jobBoardJobs.value = [];
    return;
  }
  try {
    const extraSprs = [];
    for (const [ppId, row] of Object.entries(sessionSprs.value || {})) {
      if (ppIds.has(ppId) && row?.spr_name) {
        extraSprs.push(row.spr_name);
      }
    }
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_pp_job_board",
      args: {
        pp_ids: JSON.stringify([...ppIds]),
        run_date: runDate.value,
        shift: shift.value,
        unit: filterUnit.value || headerUnit.value || undefined,
        spr_names: extraSprs.length ? JSON.stringify(extraSprs) : undefined,
      },
    });
    jobBoardJobs.value = res.message?.jobs || [];
    recordJobApiBaselines(jobBoardJobs.value);
  } catch (e) {
    console.warn("job board", e);
    jobBoardJobs.value = [];
  }
}

function enrichSelectedEntriesFromBoard() {
  if (!selectedEntries.value.length) {
    return;
  }
  let changed = false;
  const next = selectedEntries.value.map((entry) => {
    const jid = entry.jobId || entry.job_id;
    if (jid && entry.ppId) {
      // Lamination: never overwrite FG selection with fabric shaft job-board rows
      if (isLaminationMode.value) {
        const meta = orderMetaForPp(entry.ppId);
        const stats = orderDayStatsForPp(entry.ppId);
        const nextGsm = cint(entry.gsm) || cint(meta.gsm) || 0;
        const nextWidth = sprFlt(entry.width_inch) || sprFlt(meta.width_inch) || 0;
        const nextLine = meta.planningLineId || entry.lineId || "";
        const nextTgt = sprFlt(entry.dayTargetKg) || stats.dayTargetKg;
        const nextRem =
          entry.dayRemKg != null && entry.dayRemKg !== ""
            ? sprFlt(entry.dayRemKg)
            : stats.dayRemKg;
        if (
          (meta.planningLineId && meta.planningLineId !== entry.lineId) ||
          !cint(entry.gsm) ||
          (!sprFlt(entry.width_inch) && nextWidth) ||
          !sprFlt(entry.dayTargetKg) ||
          entry.dayRemKg == null ||
          entry.dayRemKg === ""
        ) {
          changed = true;
          return {
            ...entry,
            lineId: nextLine,
            quality: entry.quality || meta.quality,
            color: entry.color || meta.color,
            gsm: nextGsm,
            fabric_gsm: nextGsm,
            width_inch: nextWidth || null,
            widthLabel: entry.widthLabel || (nextWidth ? String(nextWidth) : ""),
            dayTargetKg: nextTgt,
            dayRemKg: nextRem,
            lamination_process: entry.lamination_process || meta.lamination_process || "",
          };
        }
        return entry;
      }
      const job = jobBoardJobs.value.find((j) => j.pp_id === entry.ppId && String(j.job_id) === String(jid));
      if (job) {
        const snap = snapshotFromJob(job);
        const needsLine =
          !entry.lineId ||
          String(entry.lineId).startsWith("job-") ||
          entry.lineId === "1" ||
          entry.lineId === "gsm-job";
        if (
          needsLine ||
          entry.key !== snap.key ||
          entry.combination_label !== snap.combination_label ||
          entry.quality !== snap.quality ||
          entry.color !== snap.color ||
          !entry.gsm ||
          entry.orderCode === entry.ppId ||
          sprFlt(entry.width_inch) !== sprFlt(snap.width_inch)
        ) {
          changed = true;
          return { ...entry, ...snap, key: entry.key || snap.key };
        }
      }
      return entry;
    }
    const live = lineById.value.get(entry.lineId);
    if (!live) {
      return entry;
    }
    const livePd = live.plannedDate || linePlannedDate(live.source || {});
    if (entry.ppId && entry.orderCode && entry.plannedDate === livePd) {
      return entry;
    }
    changed = true;
    return snapshotFromLine({ ...live, plannedDate: entry.plannedDate || livePd });
  });
  if (changed) {
    selectedEntries.value = next;
    scheduleAutosave();
  }
}

async function fetchLaminationOrdersForDate(overrideDate = null) {
  const d = overrideDate || ordersBrowseDate();
  const unit = filterUnit.value || headerUnit.value || LAMINATION_UNIT;
  const r = await frappe.call({
    method: "production_entry.production_planning.unified_production_entry_api.get_gsm_lamination_order_board",
    args: { run_date: d, unit },
  });
  const orders = r.message?.orders || [];
  return orders.map((o) =>
    normalizeChartRow({
      pp_id: o.pp_id,
      ppId: o.pp_id,
      pp_docstatus: o.pp_docstatus || 1,
      party_code: o.order_code || o.party_code,
      order_code: o.order_code,
      customer: o.customer,
      unit: o.unit || LAMINATION_UNIT,
      planned_date: o.planned_date || d,
      quality: o.quality,
      color: o.color,
      qty: o.target_kg || o.qty || 0,
      gsm: cint(o.gsm) || cint(o.fabric_gsm) || cint(o.lam_gsm) || 0,
      fabric_gsm: cint(o.fabric_gsm) || 0,
      lam_gsm: cint(o.lam_gsm) || 0,
      bopp_gsm: cint(o.bopp_gsm) || 0,
      combination: o.combination,
      lamination_process: o.lamination_process,
      needs_fabric_input: o.needs_fabric_input,
      needs_bopp_input: o.needs_bopp_input,
      actual_production_weight_kgs: o.produced_kg || o.actual_production_weight_kgs || 0,
      remaining_kg: o.remaining_kg,
      itemName: o.itemName || o.psi_name || o.name || "",
      name: o.itemName || o.psi_name || o.name || "",
      width_inch: o.width_inch || o.width || 0,
      width: o.width_inch || o.width || 0,
    })
  );
}

async function fetchColorChartForDate(overrideDate = null) {
  if (isLaminationUnit(filterUnit.value || headerUnit.value)) {
    return fetchLaminationOrdersForDate(overrideDate);
  }
  const args = { ...buildFetchArgs() };
  if (viewScope.value === "daily") {
    args.date = overrideDate || ordersBrowseDate();
  }
  const r = await frappe.call({
    method: "production_entry.production_planning.scheduler_api.get_color_chart_data",
    args,
  });
  return (r.message || []).map(normalizeChartRow);
}

async function fetchSessionSupplementalOrders() {
  if (!shiftOpened.value) {
    return;
  }
  const browse = ordersBrowseDate();
  const extraDates = sessionPlannedDates().filter((d) => d && d !== browse);
  if (!extraDates.length) {
    return;
  }
  let merged = rawOrders.value;
  for (const d of extraDates) {
    const extra = await fetchColorChartForDate(d);
    merged = mergeChartOrders(merged, extra);
  }
  rawOrders.value = merged;
}

async function fetchPpOrdersSupplement() {
  const browse = ordersBrowseDate();
  const unit = filterUnit.value || headerUnit.value;
  if (!browse) {
    return;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_pp_orders_for_date",
      args: { planned_date: browse, unit: unit || undefined },
    });
    const extra = (res.message || []).map(normalizeChartRow);
    if (extra.length) {
      rawOrders.value = mergeChartOrders(rawOrders.value, extra);
    }
  } catch (e) {
    console.warn("PP orders supplement", e);
  }
}

async function fetchOrders() {
  loadingOrders.value = true;
  try {
    rawOrders.value = await fetchColorChartForDate(ordersBrowseDate());
    if (!isLaminationMode.value) {
      await fetchPpOrdersSupplement();
    }
    if (!filterUnit.value && headerUnit.value) {
      filterUnit.value = headerUnit.value;
    }
    await fetchSessionSupplementalOrders();
    await Promise.all([fetchMerges(), loadQuotaForLines(), loadJobBoard()]);
    pruneSelectedEntriesToFilter();
    enrichSelectedEntriesFromBoard();
  } catch (e) {
    console.error(e);
    frappe.msgprint("Failed to load orders");
  } finally {
    loadingOrders.value = false;
  }
}

function isGsmAdminUser() {
  return (
    frappe.user.has_role("Administrator") ||
    frappe.user.has_role("System Manager")
  );
}

async function loadBayOptions() {
  const unit = (headerUnit.value || filterUnit.value || "").trim();
  if (!unit) {
    bayOptions.value = [];
    return;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_warehouse_bays_for_unit",
      args: { unit },
    });
    const list = Array.isArray(res.message) ? res.message : [];
    bayOptions.value = list;
    const allowed = new Set(list.map((b) => b.name || b.bay_name).filter(Boolean));
    for (const row of rollLines.value) {
      if (row.custom_bay && allowed.size && !allowed.has(row.custom_bay)) {
        row.custom_bay = "";
      }
    }
  } catch (e) {
    console.warn("loadBayOptions", e);
    bayOptions.value = [];
  }
}

function onBaySelectFocus() {
  if (!bayOptions.value.length) {
    void loadBayOptions();
  }
}

function onUnitChange() {
  if (shiftOpened.value && filterUnit.value !== headerUnit.value) {
    if (isGsmAdminUser()) {
      frappe.confirm(
        __(
          "Switch unit? The current shift session will be left on <b>{0}</b>. Rolls on screen will reload for <b>{1}</b>.",
          [headerUnit.value, filterUnit.value || __("none")]
        ),
        () => {
          void switchGsmUnit(filterUnit.value);
        },
        () => {
          filterUnit.value = headerUnit.value;
        }
      );
      return;
    }
    frappe.msgprint(__("Close the current shift before changing unit."));
    filterUnit.value = headerUnit.value;
    return;
  }
  void activateSelectedUnit();
}

async function switchGsmUnit(nextUnit) {
  clearGsmMixState();
  rollLines.value = [];
  sessionSprs.value = {};
  sessionJobApiBaseline.value = {};
  clearGsmUnitContextState();
  applyShiftSessionHydration(null);
  headerUnit.value = nextUnit || "";
  filterUnit.value = nextUnit || "";
  batchContextKey.value = nextUnit ? currentBatchContextKey() : "";
  if (!nextUnit) {
    scheduleAutosave();
    return;
  }
  await activateSelectedUnit({ fromAdminSwitch: true, resumingSession: false });
}

async function activateSelectedUnit(options = {}) {
  const prevUnit = headerUnit.value;
  const prevKey = batchContextKey.value || currentBatchContextKey();

  if (!filterUnit.value) {
    clearGsmUnitContextState();
    headerUnit.value = "";
    applyShiftSessionHydration(null);
    shiftSessionReady.value = true;
    batchContextKey.value = "";
    return;
  }

  headerUnit.value = filterUnit.value;
  const newKey = currentBatchContextKey();
  const unitChanged = Boolean(prevUnit && prevUnit !== filterUnit.value);
  const contextChanged = gsmContextKeyChanged(prevKey, newKey);
  if ((unitChanged || contextChanged) && !shiftOpened.value && !options.resumingSession) {
    clearGsmUnitContextState();
  }
  batchContextKey.value = newKey;
  pruneSelectedEntriesToFilter();
  await fetchMerges();
  await Promise.all([loadQuotaForLines(), loadJobBoard()]);
  pruneSelectedEntriesToFilter();
  enrichSelectedEntriesFromBoard();
  const resumed = await tryResumeOpenSessionForUnit({
    quiet: !options.fromAdminSwitch,
  });
  if (!resumed) {
    await refreshShiftSession();
  }
  await loadBayOptions();
  scheduleAutosave();
}

async function tryResumeOpenSessionForUnit(options = {}) {
  if (!headerUnit.value) {
    return false;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_open_gsm_shift_for_unit",
      args: { unit: headerUnit.value },
    });
    const sess = res.message?.session;
    if (!sess || sess.status !== "Open") {
      return false;
    }
    const sessDate = String(sess.run_date || "").slice(0, 10);
    const sessShift = sess.shift || shift.value;
    const sameContext =
      sessDate === runDate.value && sessShift === shift.value && shiftOpened.value;
    if (sameContext && rollLines.value.length) {
      return true;
    }
    if (!options.quiet && !options.fromAdminSwitch) {
      const proceed = await new Promise((resolve) => {
        frappe.confirm(
          __("{0} is already open on {1} for Unit {2}. Resume that session?", [
            sessShift,
            sessDate,
            headerUnit.value,
          ]),
          () => resolve(true),
          () => resolve(false)
        );
      });
      if (!proceed) {
        return false;
      }
    }
    runDate.value = sessDate;
    // Keep Planned Date (filterDate) independent — do not overwrite browse day
    // with session run date (caused 09 vs 10 selection / unlock conflicts).
    shift.value = sessShift;
    shiftFilterDate.value = sessDate;
    shiftFilterShift.value = sessShift;
    return await bootstrapFromServerSession({ unit: headerUnit.value });
  } catch (e) {
    console.warn("resume open session for unit", e);
    return false;
  }
}

async function promptQualityCheckJobId(ppId) {
  const fromGrid = [
    ...new Set(
      rollLines.value
        .filter((r) => r.pp_id === ppId && !cint(r.is_wasted))
        .map((r) => String(r.job_id || "").trim())
        .filter(Boolean)
    ),
  ];
  if (fromGrid.length === 1) {
    return fromGrid[0];
  }
  if (fromGrid.length > 1) {
    return new Promise((resolve) => {
      frappe.prompt(
        [
          {
            fieldtype: "Select",
            fieldname: "job_id",
            label: __("Job"),
            options: fromGrid.map((id) => ({ value: id, label: id })),
            reqd: 1,
          },
        ],
        (values) => resolve(values.job_id || fromGrid[0]),
        __("Quality Check — choose job"),
        __("Continue")
      );
    });
  }
  return "";
}

function pickEmployeeLink(fieldKey, label) {
  const targetRef =
    fieldKey === "coordinator" ? coordinator : fieldKey === "supervisor" ? supervisor : operator;
  const d = new frappe.ui.Dialog({
    title: label,
    fields: [
      {
        fieldtype: "Link",
        fieldname: "employee",
        label,
        options: "Employee",
        reqd: 1,
        default: targetRef.value || "",
        ignore_user_permissions: 1,
        get_query() {
          return {
            query: "production_entry.production_planning.unified_production_entry_api.gsm_employee_link_query",
          };
        },
      },
    ],
    primary_action_label: __("Select"),
    primary_action(values) {
      if (values.employee) {
        targetRef.value = values.employee;
      }
      d.hide();
    },
  });
  d.show();
}

function applyShiftSessionHydration(session) {
  shiftSession.value = session || null;
  if (session && session.status === "Open") {
    operator.value = session.operator || "";
    supervisor.value = session.supervisor || "";
    coordinator.value = session.coordinator || "";
    shiftBatchPrefix.value = session.batch_series_prefix || "";
    if (session.batch_series_prefix) {
      seriesPrefix.value = session.batch_series_prefix;
    }
    startShiftReminderTimers();
  } else {
    operator.value = "";
    supervisor.value = "";
    coordinator.value = "";
    shiftBatchPrefix.value = "";
    shiftResumeBanner.value = "";
    selectedEntries.value = [];
    selectionLocked.value = false;
    stopShiftReminderTimers();
  }
}

function shouldResumeFromServer() {
  if (!shiftOpened.value) {
    return false;
  }
  if (!rollLines.value.length) {
    return true;
  }
  const serverPrefix = shiftBatchPrefix.value || "";
  if (serverPrefix && seriesPrefix.value && seriesPrefix.value !== serverPrefix) {
    return true;
  }
  // Always merge when shift is open so laptop / second browser gets server rolls.
  return true;
}

function rebuildSelectedEntriesFromResume(jobSelections, options = {}) {
  const replaceAll = !!options.replaceAll;
  const serverKeys = new Set();
  const merged = [];
  const existingByKey = new Map(
    selectedEntries.value.map((e) => [e.key || entryKeyJob(e.ppId, e.jobId || e.job_id), e])
  );

  for (const row of jobSelections || []) {
    const ppId = row.pp_id || row.ppId;
    const jobId = row.job_id || row.jobId;
    if (!ppId || jobId == null || jobId === "") {
      continue;
    }
    const key = entryKeyJob(ppId, jobId);
    serverKeys.add(key);
    const job = jobBoardJobs.value.find(
      (j) => j.pp_id === ppId && String(j.job_id) === String(jobId)
    );
    if (job) {
      merged.push(snapshotFromJob(job));
      continue;
    }
    if (existingByKey.has(key)) {
      merged.push(existingByKey.get(key));
      continue;
    }
    const meta = orderMetaForPp(ppId);
    merged.push({
      key,
      jobId,
      lineId: meta.planningLineId,
      plannedDate: existingByKey.get(key)?.plannedDate || filterDate.value || "",
      ppId,
      orderCode: meta.orderCode,
      partyName: meta.partyName,
      quality: meta.quality,
      color: meta.color,
      gsm: meta.gsm || 0,
      width_inch: null,
      widthLabel: "",
      dayTargetKg: orderDayStatsForPp(ppId).dayTargetKg,
      sourceSnapshot: { pp_id: ppId },
    });
  }

  if (!replaceAll) {
    for (const entry of selectedEntries.value) {
      const key = entry.key || entryKeyJob(entry.ppId, entry.jobId || entry.job_id);
      if (!serverKeys.has(key) && !merged.some((m) => (m.key || entryKeyJob(m.ppId, m.jobId || m.job_id)) === key)) {
        merged.push(entry);
      }
    }
  }

  if (merged.length || replaceAll || !selectedEntries.value.length) {
    selectedEntries.value = merged;
  }
}

async function backfillSessionSprLabelTypes() {
  const entries = Object.entries(sessionSprs.value || {});
  const missing = entries.filter(([, s]) => s?.spr_name && !s?.label_type);
  if (!missing.length) {
    return;
  }
  const next = { ...sessionSprs.value };
  let changed = false;
  await Promise.all(
    missing.map(async ([ppId, s]) => {
      try {
        const headers = await gsmSprHeaders([s.spr_name]);
        const lt = headers[s.spr_name]?.label_type || headers[s.spr_name]?.custom_label;
        if (lt) {
          next[ppId] = { ...s, pp_id: ppId, label_type: lt };
          changed = true;
        }
      } catch (e) {
        console.warn("label backfill", ppId, e);
      }
    })
  );
  if (changed) {
    sessionSprs.value = next;
    scheduleAutosave();
  }
}

function rollRowSyncKey(row) {
  if (!row) {
    return "";
  }
  if (cint(row.is_wasted) && row.batch_no) {
    return `waste:${row.batch_no}`;
  }
  return row.batch_no || row.spr_item_name || row.roll_waste_row_name || row._id || "";
}

function enrichRollLinesDisplayMeta() {
  for (const row of rollLines.value) {
    const meta = orderMetaForPp(row.pp_id);
    if (!row.quality) {
      row.quality = meta.quality || "";
    }
    if (!row.color) {
      row.color = meta.color || row.fabric_colour || "";
    }
    if (!row.gsm && meta.gsm) {
      row.gsm = meta.gsm;
    }
    if (!row.party_code && meta.orderCode) {
      row.party_code = meta.orderCode;
    }
  }
}

function applyResumePayload(msg, options = {}) {
  if (!msg || msg.status !== "ok") {
    return 0;
  }
  const merge = !!options.merge;
  const serverRevision = msg.server_revision || msg.server_modified || "";
  if (merge && serverRevision && lastServerSyncAt.value === serverRevision) {
    return 0;
  }
  const sprMap = {};
  for (const s of msg.session_sprs || []) {
    const ppId = s.pp_id || s.ppId;
    if (ppId && s.spr_name) {
      sprMap[ppId] = {
        pp_id: ppId,
        spr_name: s.spr_name,
        order_code: s.order_code || "",
        label_type: s.label_type || "",
        is_trial: cint(s.is_trial) ? 1 : 0,
      };
    }
  }
  if (Object.keys(sprMap).length) {
    sessionSprs.value = { ...sessionSprs.value, ...sprMap };
  }
  const serverRows = (msg.roll_lines || []).map((r, idx) => {
    const isMix = !!cint(r.is_mix_roll_row);
    const hasMixProduction =
      sprFlt(r.produced_length_mtrs) > 0 &&
      sprNormalizeGrossWeightInput(r.gross_weight) > 0;
    return {
      ...r,
      ...normalizeSpiDiameterCbm(r),
      gross_weight: r.gross_weight != null && r.gross_weight !== "" ? String(r.gross_weight) : "",
      produced_length_mtrs: sprWholeMtrs(r.produced_length_mtrs),
      _id: r._id || `resume-${idx}-${Date.now()}`,
      creation_seq: cint(r.creation_seq) || rollBatchSuffix(r.batch_no) || idx + 1,
      is_bundle_row: !!cint(r.is_bundle_row),
      is_wasted: !!cint(r.is_wasted),
      is_mix_roll_row: isMix,
      custom_unit: r.custom_unit || (isMix ? headerUnit.value : "") || "",
      planned_qty: isMix ? 0 : r.planned_qty,
      row_readonly: !!cint(r.row_readonly),
      row_locked: isMix
        ? hasMixProduction && !!cint(r.row_locked)
        : r.row_locked != null
          ? !!cint(r.row_locked)
          : true,
      row_ready_for_print: isMix
        ? hasMixProduction && !!cint(r.row_ready_for_print)
        : r.row_ready_for_print != null
          ? !!cint(r.row_ready_for_print)
          : !cint(r.is_wasted),
    };
  });
  if (merge && rollLines.value.length) {
    const serverByKey = new Map();
    for (const sr of serverRows) {
      const k = rollRowSyncKey(sr);
      if (k) {
        serverByKey.set(k, sr);
      }
    }
    const merged = [...serverRows];
    for (const local of rollLines.value) {
      const k = rollRowSyncKey(local);
      if (local.is_mix_roll_row) {
        const activeSpr = _cstr(activeMixRoll.value?.spr_name);
        const keepActive = activeSpr && _cstr(local.spr_name) === activeSpr;
        const onServer = serverRows.some(
          (sr) => sr.is_mix_roll_row && sr.spr_name === local.spr_name
        );
        if (k && serverByKey.has(k) && !local.row_locked && !local.is_wasted) {
          const sr = serverByKey.get(k);
          sr.produced_length_mtrs = local.produced_length_mtrs;
          sr.gross_weight = local.gross_weight;
          sr.net_weight = local.net_weight;
          sr.produced_gsm = local.produced_gsm;
          sr.custom_diameter_inches = local.custom_diameter_inches;
          sr.custom_cbm_cubic_meters = local.custom_cbm_cubic_meters;
          sr._id = local._id || sr._id;
          sr.creation_seq = cint(local.creation_seq) || sr.creation_seq;
          sr.row_locked = 0;
          sr.row_ready_for_print = 0;
        } else if ((keepActive || onServer) && k && !serverByKey.has(k) && !local.is_wasted) {
          merged.unshift({ ...local });
        }
        continue;
      }
      if (local.row_locked || local.is_wasted) {
        continue;
      }
      if (!k || serverByKey.has(k)) {
        continue;
      }
      const wasteKey = local.batch_no ? `waste:${local.batch_no}` : "";
      if (wasteKey && serverByKey.has(wasteKey)) {
        continue;
      }
      merged.unshift({
        ...local,
        creation_seq: Math.max(cint(local.creation_seq), nextCreationSeq()),
      });
    }
    rollLines.value = sortRollLinesLifo(merged);
  } else {
    rollLines.value = sortRollLinesLifo(serverRows);
  }
  if (dismissedMixSprs.value.length) {
    rollLines.value = rollLines.value.filter(
      (r) => !r.is_mix_roll_row || !dismissedMixSprs.value.includes(_cstr(r.spr_name))
    );
  }
  rebuildSelectedEntriesFromResume(msg.job_selections || [], {
    // When selection is locked, always trust server locked_jobs (even if empty after Clear).
    replaceAll: !merge || !!cint(msg.selection_locked),
  });
  if (shiftOpened.value) {
    if (msg.selection_locked != null) {
      selectionLocked.value = !!cint(msg.selection_locked);
    } else if (!merge && ((msg.roll_lines || []).length || Object.keys(sprMap).length)) {
      selectionLocked.value = true;
    }
    if (!merge && (selectionLocked.value || (msg.job_selections || []).length)) {
      recordJobApiBaselines(jobBoardJobs.value);
    }
  }
  if (msg.session?.operator) {
    operator.value = msg.session.operator;
  }
  if (msg.session?.supervisor) {
    supervisor.value = msg.session.supervisor;
  }
  if (msg.session?.coordinator) {
    coordinator.value = msg.session.coordinator;
  }
  if (msg.session?.batch_series_prefix) {
    shiftBatchPrefix.value = msg.session.batch_series_prefix;
    if (!seriesPrefix.value) {
      seriesPrefix.value = msg.session.batch_series_prefix;
    }
  }
  rollLines.value = rollLines.value.map((r) => {
    if (!r.party_code && r.pp_id) {
      return { ...r, party_code: resolveOrderCodeForPp(r.pp_id) };
    }
    return r;
  });
  enrichRollLinesDisplayMeta();
  syncCreationSeqFromGrid();
  syncBatchCounterFromGrid();
  lastServerSyncAt.value = serverRevision || new Date().toISOString();
  scheduleAutosave();
  void backfillSessionSprLabelTypes();
  void restoreActiveMixRollFromSession();
  return serverRows.length;
}

function peekDraftFilterDate() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY.value) || localStorage.getItem("gsm_production_entry_draft_v2");
    if (!raw) {
      return null;
    }
    const d = JSON.parse(raw);
    return d.filterDate || null;
  } catch (e) {
    return null;
  }
}

async function bootstrapFromServerSession(options = {}) {
  const unit = _cstr(options.unit || filterUnit.value || headerUnit.value).trim();
  if (!unit) {
    return false;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_session_full_state",
      args: { unit },
    });
    const msg = res.message || {};
    if (msg.status !== "ok") {
      return false;
    }
    const sess = msg.session || {};
    runDate.value = String(msg.run_date || sess.run_date || runDate.value).slice(0, 10);
    shift.value = msg.shift || sess.shift || shift.value;
    headerUnit.value = msg.unit || sess.custom_unit || unit;
    filterUnit.value = headerUnit.value;
    applyShiftSessionHydration(sess);
    shiftSessionReady.value = true;
    if (sess.batch_series_prefix) {
      shiftBatchPrefix.value = sess.batch_series_prefix;
      seriesPrefix.value = sess.batch_series_prefix;
    }
    const draftFilter = peekDraftFilterDate();
    if (draftFilter) {
      filterDate.value = draftFilter;
    }
    await fetchOrders();
    if (msg.job_board?.jobs?.length) {
      jobBoardJobs.value = msg.job_board.jobs;
    } else {
      await loadJobBoard();
    }
    const count = applyResumePayload(msg, { merge: false });
    await fetchSessionSupplementalOrders();
    await loadJobBoard();
    enrichSelectedEntriesFromBoard();
    if (count > 0) {
      shiftResumeBanner.value = __("Resumed {0} roll line(s) from server.", [count]);
      frappe.show_alert({ message: shiftResumeBanner.value, indicator: "green" });
    }
    setupGsmLiveSync();
    await loadMixRollCandidates();
    await loadBayOptions();
    return true;
  } catch (e) {
    console.warn("bootstrap session", e);
    return false;
  }
}

async function refreshSessionFromServer(options = {}) {
  if (!headerUnit.value || !runDate.value || !shift.value || !shiftOpened.value) {
    return 0;
  }
  if (!gsmPageIsVisible() && options.quiet) {
    return 0;
  }
  if (gsmRefreshInFlight) {
    gsmRefreshQueued = true;
    return 0;
  }
  gsmRefreshInFlight = true;
  try {
    if (!jobBoardJobs.value.length) {
      await loadJobBoard();
    }
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_active_shift_resume",
      type: "POST",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
      },
      error: () => {},
    });
    const merge = options.merge !== false;
    const msg = res.message || {};
    if (msg.status !== "ok") {
      return 0;
    }
    const count = applyResumePayload(msg, { merge });
    liveSyncLabel.value = __("Live sync · {0} rolls", [rollLines.value.length]);
    if (count > 0 && !options.quiet) {
      frappe.show_alert({ message: __("Synced {0} roll(s) from server", [count]), indicator: "blue" });
    }
    await fetchSessionSupplementalOrders();
    await loadJobBoard();
    enrichSelectedEntriesFromBoard();
    return count;
  } catch (e) {
    console.warn("refresh session", e);
    if (isGsmSessionDeadError(e)) {
      markGsmSessionExpired();
    }
    return 0;
  } finally {
    gsmRefreshInFlight = false;
    if (gsmRefreshQueued && gsmPageIsVisible()) {
      gsmRefreshQueued = false;
      setTimeout(() => refreshSessionFromServer({ quiet: true, merge: true }), 300);
    }
  }
}

async function resumeActiveShiftFromServer(options = {}) {
  if (!headerUnit.value || !runDate.value || !shift.value || !shiftOpened.value) {
    return 0;
  }
  return refreshSessionFromServer({ ...options, merge: options.merge !== false });
}

async function syncOpenShiftForUnit() {
  if (!headerUnit.value) {
    return false;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_open_gsm_shift_for_unit",
      args: { unit: headerUnit.value },
    });
    const sess = res.message?.session;
    if (!sess || sess.status !== "Open") {
      return false;
    }
    const sessDate = String(sess.run_date || "").slice(0, 10);
    const sessShift = sess.shift || shift.value;
    if (sessDate === runDate.value && sessShift === shift.value) {
      return false;
    }
    return await new Promise((resolve) => {
      frappe.confirm(
        __("{0} is already open on {1} for Unit {2}. Switch to that session?", [
          sessShift,
          sessDate,
          headerUnit.value,
        ]),
        () => {
          runDate.value = sessDate;
          // Keep Planned Date independent of session run date.
          shift.value = sessShift;
          shiftFilterDate.value = sessDate;
          shiftFilterShift.value = sessShift;
          resolve(true);
        },
        () => resolve(false)
      );
    });
  } catch (e) {
    console.warn("open shift for unit", e);
    return false;
  }
}

async function loadShiftStatusForDate() {
  if (!headerUnit.value || !runDate.value) {
    shiftStatusByShift.value = {};
    return;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_sessions_for_date",
      args: {
        run_date: runDate.value,
        unit: headerUnit.value,
      },
    });
    shiftStatusByShift.value = res.message?.shifts || {};
  } catch (e) {
    console.warn("shift status", e);
    shiftStatusByShift.value = {};
  }
}

async function previewShiftBatchPrefix() {
  if (!headerUnit.value || !runDate.value || !shift.value || shiftOpened.value) {
    return;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.preview_gsm_shift_batch_prefix",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
      },
    });
    shiftPreviewBatch.value = res.message?.series_prefix || "";
    if (res.message?.reused && res.message?.reused_from_shift) {
      shiftBatchReuseNotice.value = __(
        "Reusing unused batch {0} from {1}.",
        [res.message.series_prefix, res.message.reused_from_shift]
      );
    }
  } catch (e) {
    console.warn("shift batch preview", e);
    shiftPreviewBatch.value = "";
  }
}

async function refreshShiftSession() {
  if (!headerUnit.value || !runDate.value || !shift.value) {
    shiftSessionReady.value = true;
    shiftSession.value = null;
    shiftPreviewBatch.value = "";
    await loadShiftStatusForDate();
    return;
  }
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_session",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
      },
    });
    const session = res.message?.session || null;
    applyShiftSessionHydration(session);
    await loadShiftStatusForDate();
    if (session) {
      shiftPreviewBatch.value = session.batch_series_prefix || "";
    } else {
      shiftPreviewBatch.value = "";
    }
    if (session && session.status === "Open" && shouldResumeFromServer()) {
      await resumeActiveShiftFromServer({ quiet: true });
    }
    await loadBayOptions();
  } catch (e) {
    console.warn("shift session", e);
    applyShiftSessionHydration(null);
  } finally {
    shiftSessionReady.value = true;
  }
}

async function startShift() {
  if (!canConfirmShiftOpen.value) {
    frappe.msgprint(__("Complete operator, supervisor, co-ordinator, and re-open reason if required."));
    return;
  }
  shiftOpeningBusy.value = true;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.open_gsm_shift_session",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
        operator: operator.value,
        supervisor: supervisor.value,
        coordinator: coordinator.value,
        reopen_reason: shiftReopenRequired.value ? shiftReopenReason.value : undefined,
        reopen_remarks: shiftReopenRequired.value ? shiftReopenRemarks.value : undefined,
      },
    });
    applyShiftSessionHydration(res.message?.session || null);
    const reused = !!(res.message?.reused);
    if (reused) {
      await resumeActiveShiftFromServer({ quiet: true });
      if (!rollLines.value.length && !Object.keys(sessionSprs.value || {}).length) {
        selectionLocked.value = false;
        selectedEntries.value = [];
      }
    } else {
      rollLines.value = [];
      sessionSprs.value = {};
      selectionLocked.value = false;
      selectedEntries.value = [];
      forceNewSprSession.value = true;
      resetBatchSeriesForShiftOpen();
    }
    await loadShiftStatusForDate();
    await loadBayOptions();
    scheduleAutosave();
    showShiftOpenDialog.value = false;
    shiftReopenReason.value = "";
    shiftReopenRemarks.value = "";
    frappe.show_alert({ message: __("Shift opened"), indicator: "green" });
  } catch (e) {
    console.error(e);
  } finally {
    shiftOpeningBusy.value = false;
  }
}

function clearGsmAfterClose() {
  selectionLocked.value = false;
  selectedEntries.value = [];
  rollLines.value = [];
  mixRollLines.value = [];
  activeMixRoll.value = null;
  persistedActiveMixSpr.value = "";
  mixRollCandidates.value = [];
  sessionSprs.value = {};
  sessionJobApiBaseline.value = {};
  operator.value = "";
  supervisor.value = "";
  coordinator.value = "";
  forceNewSprSession.value = true;
  resetBatchSeriesCache();
  try {
    localStorage.removeItem(STORAGE_KEY.value);
    localStorage.removeItem("gsm_production_entry_draft_v2");
  } catch (e) {
    console.warn("draft clear", e);
  }
  saveStatus.value = "";
}

function applyShiftWisePrefill(doctype, opts) {
  const frm = typeof cur_frm !== "undefined" ? cur_frm : null;
  if (!frm || frm.doctype !== doctype || !frm.is_new() || !opts) {
    return;
  }
  // Set Employee ID link fields first so fetch_from can fill names.
  const idFirst = [
    "custom_operator1",
    "custom_supervisor1",
    "coordinator",
    "operator",
    "supervisor",
    "custom_coordinator",
  ];
  const ordered = [
    ...idFirst.filter((k) => Object.prototype.hasOwnProperty.call(opts, k)),
    ...Object.keys(opts).filter((k) => !idFirst.includes(k)),
  ];
  ordered.forEach((fname) => {
    const val = opts[fname];
    if (!val || !frm.fields_dict[fname]) {
      return;
    }
    if (!frm.doc[fname]) {
      frm.set_value(fname, val);
    }
  });
}

function redirectToShiftWise(redirect) {
  const payload = redirect || {};
  const opts = payload.route_options || {};
  const doctype = payload.doctype || "Shift Wise Production Entry";
  frappe.route_options = { ...opts };
  const afterRoute = () => {
    setTimeout(() => applyShiftWisePrefill(doctype, opts), 350);
  };
  const routed = frappe.set_route("Form", doctype, "new");
  if (routed && typeof routed.then === "function") {
    routed.then(afterRoute);
  } else {
    afterRoute();
  }
}

function dismissShiftReminder() {
  showShiftReminder.value = false;
  shiftReminderDismissedAt = Date.now();
}

function closeShiftFromReminder() {
  showShiftReminder.value = false;
  closeShift();
}

function stopShiftReminderTimers() {
  if (shiftReminderTimer) {
    clearTimeout(shiftReminderTimer);
    shiftReminderTimer = null;
  }
  if (shiftReminderInterval) {
    clearInterval(shiftReminderInterval);
    shiftReminderInterval = null;
  }
  showShiftReminder.value = false;
}

function startShiftReminderTimers() {
  stopShiftReminderTimers();
  const thirtyMin = 30 * 60 * 1000;
  shiftReminderTimer = setTimeout(() => {
    if (shiftOpened.value) {
      showShiftReminder.value = true;
    }
  }, thirtyMin);
  shiftReminderInterval = setInterval(() => {
    if (!shiftOpened.value) {
      return;
    }
    const sinceDismiss = Date.now() - shiftReminderDismissedAt;
    if (sinceDismiss >= thirtyMin) {
      showShiftReminder.value = true;
    }
  }, thirtyMin);
}

async function closeShift() {
  if (!shiftOpened.value || shiftClosingBusy.value) {
    return;
  }
  if (!(await ensureGsmDeskSession())) {
    return;
  }
  shiftClosingBusy.value = true;
  try {
    const validation = await gsmSafeCall({
      method: "production_entry.production_planning.unified_production_entry_api.validate_gsm_shift_close",
      type: "POST",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
        session_sprs: shiftSessionSprList.value,
      },
    });
    const errors = validation.message?.errors || [];
    if (errors.length) {
      frappe.msgprint(errors.join("<br>"));
      return;
    }
    await new Promise((resolve, reject) => {
      frappe.confirm(
        __("Close shift? This will clear GSM entries and open Shift Wise Production Entry."),
        () => resolve(),
        () => reject(new Error("cancelled"))
      );
    });
    const res = await gsmSafeCall({
      method: "production_entry.production_planning.unified_production_entry_api.close_gsm_shift_session",
      type: "POST",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
        operator: operator.value,
        supervisor: supervisor.value,
        coordinator: coordinator.value,
      },
    });
    const nextShift = res.message?.next_shift;
    clearGsmAfterClose();
    applyShiftSessionHydration(null);
    if (nextShift) {
      shift.value = nextShift;
      shiftFilterShift.value = nextShift;
    }
    await refreshShiftSession();
    redirectToShiftWise(res.message?.redirect);
  } catch (e) {
    if (e?.message === "cancelled" || e?.message === "session expired") {
      return;
    }
    console.error(e);
    if (isGsmSessionDeadError(e)) {
      markGsmSessionExpired();
    }
  } finally {
    shiftClosingBusy.value = false;
  }
}

function onShiftHeaderChange() {
  shiftReopenReason.value = "";
  shiftReopenRemarks.value = "";
  if (!shiftOpened.value) {
    operator.value = "";
    supervisor.value = "";
    coordinator.value = "";
  }
  refreshShiftSession();
  scheduleAutosave();
}

async function loadCurrentShift() {
  try {
    const r = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_current_shift",
    });
    if (r.message) {
      shift.value = r.message;
      shiftFilterShift.value = r.message;
    }
  } catch (e) {
    console.warn("shift default", e);
  }
}

async function loadGsmBoardAccess() {
  try {
    const r = await frappe.call({
      method: "production_entry.production_planning.board_access.get_production_board_user_context",
      args: { board_slug: GSM_BOARD_SLUG.value },
    });
    const scope = (r && r.message) || { unlimited: true, allowed_units: [], permitted: true };
    gsmBoardAccess.value = { ...scope, loaded: true };
    if (!scope || scope.unlimited) {
      gsmUnitFilterState.value = { pool: null, showUnitFilter: true, unitLocked: false };
    } else {
      gsmUnitFilterState.value = applyBoardAccessUnitScope(scope, filterUnit, pageEntryUnits.value);
    }
  } catch (e) {
    console.warn("gsm board access", e);
    gsmBoardAccess.value = { unlimited: true, allowed_units: [], loaded: true, permitted: true, frozen_actions: {} };
    gsmUnitFilterState.value = { pool: null, showUnitFilter: true, unitLocked: false };
  }
  scrubUnitSelectionToPage();
}

/** Drop unit selections that do not belong on this page; auto-pick when only one unit. */
function scrubUnitSelectionToPage() {
  const allowed = pageEntryUnits.value.map((u) => normalizeGsmUnit(u));
  const ok = (u) => {
    const n = normalizeGsmUnit(u);
    return n && allowed.includes(n);
  };
  const fallback = allowed.length === 1 ? allowed[0] : "";
  if (filterUnit.value && !ok(filterUnit.value)) {
    filterUnit.value = fallback;
  }
  if (headerUnit.value && !ok(headerUnit.value)) {
    headerUnit.value = filterUnit.value || fallback;
  }
  if (!filterUnit.value && fallback) {
    filterUnit.value = fallback;
    headerUnit.value = fallback;
  }
}

async function retryPageLoad() {
  pageLoadError.value = false;
  try {
    await Promise.all([loadCurrentShift(), loadGsmBoardAccess()]);
    await loadCoreWidthOptions();
    shiftFilterDate.value = runDate.value;
    shiftFilterShift.value = shift.value;
    await fetchOrders();
    restoreDraft({ skipRollGrid: true });
    if (filterUnit.value) {
      headerUnit.value = filterUnit.value;
      shiftFilterUnit.value = filterUnit.value;
      batchContextKey.value = currentBatchContextKey();
      await refreshShiftSession();
      if (shiftOpened.value && shouldResumeFromServer()) {
        await refreshSessionFromServer({ merge: true });
      }
      if (shiftOpened.value) {
        setupGsmLiveSync();
        await loadMixRollCandidates();
      }
    } else {
      shiftFilterUnit.value = "";
      batchContextKey.value = currentBatchContextKey();
      shiftSessionReady.value = true;
    }
  } catch (e) {
    console.error("GSM page retry load error", e);
    pageLoadError.value = true;
  }
}

async function fetchQuotaForLine(line) {
  try {
    const src = line.source;
    const quotaArgs = quotaContextArgs();
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_pt_line_roll_quota_status",
      args: {
        pp_id: line.ppId || src.pp_id,
        production_plan_item: line.id,
        gsm: line.gsm,
        width_inch: line.width_inch,
        item_code: src.itemCode || src.item_code,
        run_date: quotaArgs.run_date,
        shift: quotaArgs.shift,
        unit: quotaArgs.unit,
      },
    });
    return res.message || {};
  } catch (e) {
    return {};
  }
}

async function resolveOrderLength(line, jobId = null) {
  const src = line.source;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_order_length_for_pt_line",
      args: {
        pp_id: line.ppId || src.pp_id,
        gsm: line.gsm,
        width_inch: line.width_inch,
        item_code: src.itemCode || src.item_code,
        production_plan_item: line.id,
        job_id: jobId != null && jobId !== "" ? String(jobId) : undefined,
      },
    });
    return sprFlt(res.message?.meter_roll_mtrs);
  } catch (e) {
    return sprFlt(src.meter || src.meter_roll);
  }
}

async function resolveWorkOrder(line, jobId = null) {
  const src = line.source;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.resolve_work_order_for_roll_line",
      args: {
        pp_id: line.ppId || src.pp_id,
        gsm: line.gsm,
        width_inch: line.width_inch,
        item_code: src.itemCode || src.item_code,
        production_plan_item: line.id,
        job_id: jobId != null && jobId !== "" ? String(jobId) : undefined,
      },
    });
    const msg = res.message || {};
    return {
      work_order: msg.work_order || "",
      production_item: msg.production_item || "",
      production_item_name: msg.production_item_name || "",
    };
  } catch (e) {
    return { work_order: "", production_item: "", production_item_name: "" };
  }
}

function syncBatchCounterFromGrid() {
  let mx = 0;
  const reserved = new Set();
  for (const r of rollLines.value) {
    const bn = _cstr(r.batch_no || "");
    if (bn) {
      reserved.add(bn);
    }
    const rn = parseInt(r.roll_no, 10);
    if (!Number.isNaN(rn)) {
      mx = Math.max(mx, rn);
    }
    if (bn.includes("/")) {
      const suf = parseInt(bn.split("/").pop(), 10);
      if (!Number.isNaN(suf)) {
        mx = Math.max(mx, suf);
      }
    }
  }
  maxRollSuffix.value = mx;
  reservedBatchNos.value = reserved;
}

function _cstr(v) {
  return v == null ? "" : String(v).trim();
}

function fabricWidthToStockCoreInch(widthInch) {
  const w = sprFlt(widthInch);
  if (w <= 0) {
    return 63;
  }
  for (const k of [63, 85, 90, 118, 126]) {
    if (Math.abs(w - k) < 0.6) {
      return k;
    }
  }
  if (w < 63) {
    return 63;
  }
  if (w < 85) {
    return 85;
  }
  if (w < 90) {
    return 90;
  }
  if (w < 118) {
    return 118;
  }
  return 126;
}

function pickCoreForFabricWidth(widthInch, apiCoreValue) {
  const opts = coreWidthOptions.value || [];
  const raw = _cstr(apiCoreValue);
  if (raw && opts.some((o) => o.value === raw)) {
    return raw;
  }
  const targetInch = fabricWidthToStockCoreInch(widthInch);
  const byInch = opts.find((o) => Math.abs(sprFlt(o.core_inch) - targetInch) < 0.6);
  if (byInch?.value) {
    return byInch.value;
  }
  const labelMatch = opts.find(
    (o) =>
      _cstr(o.label).startsWith(String(targetInch)) ||
      _cstr(o.value).startsWith(String(targetInch))
  );
  if (labelMatch?.value) {
    return labelMatch.value;
  }
  return raw || opts[0]?.value || "";
}

async function fetchRollRowExtras(line, lengthM, jobId = null) {
  const src = line.source;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_roll_row_extras",
      args: {
        gsm: line.gsm,
        width_inch: line.width_inch,
        length_m: lengthM || sprFlt(src.meter || src.meter_roll),
        item_code: src.itemCode || src.item_code,
        pp_id: line.ppId || src.pp_id,
        production_plan_item: line.id,
        job_id: jobId || line.jobId || line.job_id || "",
      },
    });
    return res.message || {};
  } catch (e) {
    return {
      planned_qty: 0,
      custom_polybag_kgs: 0,
      custom_core_width_mm: pickCoreForFabricWidth(line.width_inch, ""),
      core_width_mm: 0,
    };
  }
}

async function loadCoreWidthOptions() {
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_core_width_options",
    });
    const rows = res.message || [];
    if (rows.length) {
      coreWidthOptions.value = rows.map((r) => ({
        value: r.core_size || r.value || r.item_code || "",
        core_size: r.core_size || r.value || "",
        item_code: r.item_code || "",
        label: r.label || r.core_size || r.value || `${r.width_mm} mm`,
        width_mm: sprFlt(r.width_mm) || 1600,
        core_inch: sprFlt(r.core_inch) || 0,
        base_weight_kgs: sprFlt(r.base_weight_kgs) || 0,
      }));
    }
  } catch (e) {
    console.warn("core width options", e);
  }
}

function pickJobAndWidthForRow() {
  if (mixSprReady.value && !selectedEntries.value.length) {
    const mixExisting = mixRowsForActive();
    if (mixExisting.length >= mixRollMaxRows(activeMixRoll.value)) {
      frappe.msgprint(
        __("This mix roll allows {0} shaft(s) with combination {1}.", [
          mixRollShaftCount(activeMixRoll.value),
          activeMixRoll.value.shaft || activeMixRoll.value.combination || "",
        ])
      );
      return Promise.resolve(null);
    }
    return Promise.resolve({ is_mix: true });
  }
  if (!selectedEntries.value.length) {
    return Promise.resolve(null);
  }
  const entries = selectedEntries.value;
  if (mixSprReady.value) {
    addRollWizardSkipJobStep.value = false;
    addRollWizardStep.value = 1;
    addRollJobChoice.value = defaultAddRollJobKey(entries);
    addRollWidthChoice.value = null;
    showAddRollWizard.value = true;
    return new Promise((resolve) => {
      pendingAddRowResolve = resolve;
    });
  }
  if (entries.length === 1) {
    const entry = entries[0];
    const key = entry.key || entryKeyJob(entry.ppId, entry.jobId || entry.job_id);
    addRollJobChoice.value = key;
    const rawJob = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === key);
    // Lamination: no fabric shaft job board — use locked selection width/GSM
    if (!rawJob && isLaminationMode.value) {
      const widthInch = sprFlt(entry.width_inch || entry.width || 0);
      if (widthInch <= 0) {
        frappe.msgprint(__("This order has no width. Check Planning Sheet / color chart."));
        return Promise.resolve(null);
      }
      return Promise.resolve({ job: syntheticLamJobFromEntry(entry), widthInch });
    }
    if (!rawJob) {
      return Promise.resolve(null);
    }
    const job = withLocalPendingQuota(rawJob);
    const addable = (job.width_segments || []).filter((s) => s.can_add);
    const maxed = !canJobAddOneMoreRoll(job);
    if (!maxed && addable.length === 1) {
      return Promise.resolve({ job: rawJob, widthInch: addable[0].width_inch });
    }
    addRollWizardSkipJobStep.value = true;
    addRollWizardStep.value = 2;
    addRollWidthChoice.value =
      addable[0]?.width_inch ?? job.width_segments?.[0]?.width_inch ?? null;
    showAddRollWizard.value = true;
    return new Promise((resolve) => {
      pendingAddRowResolve = resolve;
    });
  }
  addRollWizardSkipJobStep.value = false;
  addRollWizardStep.value = 1;
  addRollJobChoice.value = defaultAddRollJobKey(entries);
  addRollWidthChoice.value = null;
  showAddRollWizard.value = true;
  return new Promise((resolve) => {
    pendingAddRowResolve = resolve;
  });
}

function cancelAddRollWizard() {
  showAddRollWizard.value = false;
  addRollWizardStep.value = 1;
  addRollWizardSkipJobStep.value = false;
  if (pendingAddRowResolve) {
    pendingAddRowResolve(null);
    pendingAddRowResolve = null;
  }
}

function manualJobContextForPp(ppId) {
  const lineIds = [
    ...new Set(
      selectedEntries.value
        .filter((e) => e.ppId === ppId)
        .map((e) => e.lineId)
        .filter(Boolean)
    ),
  ];
  return { ppId, planningNames: lineIds };
}

async function openManualJobForPp(ppId) {
  if (!ppId) {
    frappe.msgprint(__("No order selected for Manual Job."));
    return;
  }
  const ctx = manualJobContextForPp(ppId);
  await gsmOpenManualJob(
    ctx.ppId,
    ctx.planningNames,
    headerUnit.value,
    runDate.value,
    shift.value,
    () => Promise.all([fetchOrders(), loadJobBoard()])
  );
}

async function openManualJobFromWizard() {
  const job = wizardSelectedJob.value;
  cancelAddRollWizard();
  if (job) {
    await openManualJobForPp(job.pp_id);
  }
}

async function openManualJobForAllMaxed() {
  const ppIds = [...new Set(selectedEntries.value.map((e) => e.ppId).filter(Boolean))];
  if (!ppIds.length) {
    return;
  }
  if (ppIds.length === 1) {
    await openManualJobForPp(ppIds[0]);
    return;
  }
  const options = ppIds.map((ppId) => {
    const meta = orderMetaForPp(ppId);
    return { value: ppId, label: meta.orderCode || ppId };
  });
  frappe.prompt(
    [
      {
        fieldtype: "Select",
        fieldname: "pp_id",
        label: __("Order for Manual Job"),
        options,
        reqd: 1,
        default: ppIds[0],
      },
    ],
    (values) => openManualJobForPp(values.pp_id),
    __("Choose order for Manual Job"),
    __("Open")
  );
}

function proceedAddRollWizard() {
  if (addRollJobChoice.value === MIX_WIZARD_KEY) {
    showAddRollWizard.value = false;
    addRollWizardStep.value = 1;
    addRollWizardSkipJobStep.value = false;
    if (pendingAddRowResolve) {
      pendingAddRowResolve({ is_mix: true });
      pendingAddRowResolve = null;
    }
    return;
  }
  if (addRollWizardStep.value === 1) {
    if (!addRollJobChoice.value) {
      return;
    }
    addRollWizardStep.value = 2;
    const segs = wizardWidthSegments.value;
    const pick = segs.find((s) => s.can_add) || segs[0];
    addRollWidthChoice.value = pick?.width_inch ?? null;
    return;
  }
  const key = addRollJobChoice.value;
  let rawJob = jobBoardJobs.value.find((j) => entryKeyJob(j.pp_id, j.job_id) === key);
  const widthInch = sprFlt(addRollWidthChoice.value);
  if (!rawJob && isLaminationMode.value && widthInch > 0) {
    const entry =
      selectedEntries.value.find((e) => (e.key || entryKeyJob(e.ppId, e.jobId || e.job_id)) === key) ||
      selectedEntries.value[0];
    if (entry) {
      rawJob = syntheticLamJobFromEntry(entry);
    }
  }
  showAddRollWizard.value = false;
  addRollWizardStep.value = 1;
  addRollWizardSkipJobStep.value = false;
  if (pendingAddRowResolve) {
    pendingAddRowResolve(rawJob && widthInch > 0 ? { job: rawJob, widthInch } : null);
    pendingAddRowResolve = null;
  }
}

function _sprNotCreatedMsg(action) {
  frappe.msgprint(__("First click Create SPR, then click {0}.", [action]));
}

function guardedAddRollRow() {
  if (freezeGsmAddRow.value) { frappe.msgprint(__("Add Roll Row is disabled for your access.")); return; }
  if (mixSprReady.value && !selectedEntries.value.length) {
    if (mixRollBusy.value) return;
    addMixRollRow();
    return;
  }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Add Roll Row"); return; }
  if (!canAddRow.value || addRollInProgress.value) {
    if (mixSprReady.value && selectedEntries.value.length) {
      addRollRow();
      return;
    }
    return;
  }
  addRollRow();
}
function guardedStartShift() {
  if (freezeGsmStartShift.value) { frappe.msgprint(__("Start Shift is disabled for your access.")); return; }
  openShiftDialog();
}
function guardedMixingSheet() {
  if (freezeGsmMixingSheet.value) { frappe.msgprint(__("Mixing Sheet is disabled for your access.")); return; }
  openMixingDialog();
}
function guardedRemoveRow() {
  if (freezeGsmRemoveRow.value) { frappe.msgprint(__("Remove Row is disabled for your access.")); return; }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Remove Row"); return; }
  if (!rollLines.value.length) return;
  promptRemoveRowByBatch();
}
function guardedClearEntries() {
  if (freezeGsmClearEntries.value) { frappe.msgprint(__("Clear Entries is disabled for your access.")); return; }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Clear Entries"); return; }
  clearGridEntries();
}
function guardedToolsToggle() {
  if (freezeGsmTools.value) { frappe.msgprint(__("Tools menu is disabled for your access.")); return; }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Tools"); return; }
  toolsMenuOpen.value = !toolsMenuOpen.value;
}
function guardedShaftDetails() {
  if (freezeGsmShaftDetails.value) { frappe.msgprint(__("Shaft Details is disabled for your access.")); return; }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Shaft Details"); return; }
  openShaftDetails();
}
function guardedSubmitEntry() {
  if (freezeGsmSubmit.value) { frappe.msgprint(__("Submit Entry is disabled for your access.")); return; }
  if (!sprCreatedForSession.value) { _sprNotCreatedMsg("Submit Entry"); return; }
  ensureGsmDeskSession().then((ok) => {
    if (ok) {
      openSubmitConfirmDialog();
    }
  });
}

async function addLaminationRollRowsViaSpr() {
  if (!selectionLocked.value) {
    frappe.msgprint(__("Confirm and lock your order selection first."));
    return;
  }
  if (!selectedSessionSprList.value.length) {
    frappe.msgprint(__("Click Create SPRs before adding roll rows."));
    return;
  }
  const target = await resolveLaminationSprTarget();
  if (!target?.spr_name) {
    return;
  }
  const sprName = target.spr_name;
  const jobId =
    selectedEntries.value.find((e) => e.ppId === target.ppId)?.jobId ||
    selectedEntries.value.find((e) => e.ppId === target.ppId)?.job_id ||
    "";

  // One click = one roll line (no count prompt). Click again to add the next.
  frappe.dom.freeze(__("Adding roll line…"));
  try {
    const r = await frappe.call({
      method:
        "production_entry.production_planning.unified_production_entry_api.gsm_lamination_add_output_rolls",
      args: {
        spr_name: sprName,
        job_id: jobId || undefined,
        exact_roll_lines: 1,
      },
    });
    const added = cint(r.message?.added || r.message?.roll_lines_added || 1);
    frappe.show_alert({
      message: __("Added {0} roll line on {1}", [added, sprName]),
      indicator: "green",
    });
    await refreshSessionFromServer({ quiet: true, merge: true });
    await fetchOrders();
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Could not add roll line to Shaft Production Run."));
  } finally {
    frappe.dom.unfreeze();
  }
}

async function addRollRow() {
  if (addRollInProgress.value) {
    return;
  }
  // Lamination: same as SPR Create Entry — prompt for roll line count, no fabric shaft limit
  if (isLaminationMode.value) {
    addRollInProgress.value = true;
    try {
      await addLaminationRollRowsViaSpr();
    } finally {
      addRollInProgress.value = false;
    }
    return;
  }
  if (!selectionLocked.value) {
    frappe.msgprint("Confirm and lock your GSM selection first.");
    return;
  }
  if (!selectedSessionSprList.value.length && !mixSprReady.value) {
    frappe.msgprint(__("Click Create SPRs before adding roll rows."));
    return;
  }
  if (!headerUnit.value) {
    frappe.msgprint("Select a unit filter first.");
    return;
  }
  addRollInProgress.value = true;
  try {
  const pick = await pickJobAndWidthForRow();
  if (!pick) {
    return;
  }
  if (pick.is_mix) {
    await addMixRollRow();
    return;
  }
  const { widthInch, job: pickedJob } = pick;
  const rawJob =
    jobBoardJobs.value.find(
      (j) => j.pp_id === pickedJob.pp_id && String(j.job_id) === String(pickedJob.job_id)
    ) || pickedJob;
  const job = withLocalPendingQuota(rawJob);
  const jobId = job.job_id;
  if (ppNeedsNewSpr(job.pp_id)) {
    saveStatus.value = "Creating SPR for this order…";
    const sprName = await ensureSprForPp(job.pp_id);
    if (!sprName) {
      frappe.msgprint(__("Could not create SPR for this order. Click Create SPR for new order, then add the row again."));
      return;
    }
  }
  if (!canJobAddOneMoreRoll(job)) {
    frappe.confirm(
      __("Job roll limit reached ({0}/{1}) — use Manual Job. Open Manual Job now?", [
        effectiveJobRollCount(job),
        job.max_rolls,
      ]),
      async () => {
        const ctx = toolsContext.value;
        if (ctx) {
          await gsmOpenManualJob(
            ctx.ppId,
            ctx.planningNames,
            headerUnit.value,
            runDate.value,
            shift.value,
            () => Promise.all([fetchOrders(), loadJobBoard()])
          );
        }
      }
    );
    return;
  }
  if (!canJobAddWidthRoll(job, widthInch)) {
    const seg = (job.width_segments || []).find((s) => Math.abs(sprFlt(s.width_inch) - widthInch) < 0.05);
    frappe.confirm(
      __("Width {0}\" is full ({1}/{2}) — use Manual Job. Open Manual Job now?", [
        widthInch,
        effectiveWidthRollCount(job, widthInch),
        seg?.max || job.max_rolls,
      ]),
      async () => {
        const ctx = toolsContext.value;
        if (ctx) {
          await gsmOpenManualJob(
            ctx.ppId,
            ctx.planningNames,
            headerUnit.value,
            runDate.value,
            shift.value,
            () => Promise.all([fetchOrders(), loadJobBoard()])
          );
        }
      }
    );
    return;
  }
  const baseLine = firstPtLineForPp(job.pp_id, job.gsm, jobId);
  if (!baseLine) {
    frappe.msgprint(__("No planning line found for this order."));
    return;
  }
  lastAddRollJobKey.value = entryKeyJob(job.pp_id, jobId);
  const srcBase = baseLine.source || {};
  const line = {
    ...baseLine,
    ppId: job.pp_id,
    gsm: job.gsm,
    width_inch: widthInch,
    widthLabel: `${widthInch}"`,
    source: {
      ...srcBase,
      pp_id: job.pp_id,
      gsm: job.gsm,
      meter_roll: job.meter_roll,
      meter: job.meter_roll,
    },
  };
  const src = line.source;
  const isManualJob = !!job.is_manual;
  const [batchInfo, ordLenFromApi, woInfo] = await Promise.all([
    previewNextBatch(line.ppId),
    isManualJob || job.meter_roll ? Promise.resolve(0) : resolveOrderLength(line, jobId),
    isManualJob
      ? Promise.resolve({
          work_order: job.work_order || "",
          production_item: job.item_code || "",
          production_item_name: job.item_name || "",
        })
      : resolveWorkOrder(line, jobId),
  ]);
  const ordLen = sprFlt(job.meter_roll) || sprFlt(ordLenFromApi);
  let batch = batchInfo;
  let attempts = 0;
  while (batch?.batch_no && rollLines.value.some((r) => r.batch_no === batch.batch_no) && attempts < 12) {
    attempts += 1;
    batch = await previewNextBatch(line.ppId);
  }
  if (!batch?.batch_no || rollLines.value.some((r) => r.batch_no === batch.batch_no)) {
    frappe.msgprint(__("Could not assign a unique batch number. Try again."));
    return;
  }
  const extras = await fetchRollRowExtras(line, ordLen, jobId);
  const coreItem = pickCoreForFabricWidth(
    line.width_inch,
    extras.custom_core_width_mm || extras.core_size || ""
  );
  const itemCode = woInfo?.production_item || src.itemCode || src.item_code;
  const itemName = woInfo?.production_item_name || src.description || src.item_name || "";
  const meta = orderMetaForPp(job.pp_id);
  const rowSeq = nextCreationSeq();
  const newRow = {
    _id: `row-${Date.now()}-${rowSeq}`,
    creation_seq: rowSeq,
    planning_table_row: line.id,
    pp_id: line.ppId || src.pp_id,
    party_code: line.orderCode || resolveOrderCodeForPp(line.ppId || src.pp_id),
    item_code: itemCode,
    item_name: itemName,
    quality: src.quality || meta.quality || "",
    color: src.color || src.fabric_colour || meta.color || "",
    gsm: job.gsm,
    batch_no: batch.batch_no || "",
    roll_no: batch.roll_no || "",
    width_inch: line.width_inch,
    meter_roll: ordLen,
    produced_length_mtrs: "",
    produced_gsm: 0,
    net_weight: 0,
    gross_weight: "",
    planned_qty: sprFlt(extras.planned_qty),
    uom: src.uom || src.stock_uom || "Kg",
    custom_core_width_mm: coreItem,
    custom_polybag_kgs: extras.custom_polybag_kgs || 0,
    custom_diameter_inches: "",
    custom_cbm_cubic_meters: "",
    custom_bay: "",
    work_order: woInfo?.work_order || "",
    job_id: jobId,
    custom_no_of_shaft: currentShaftNoForJob(job),
    row_locked: 0,
    row_ready_for_print: 0,
    core_width_options: coreWidthOptions.value,
  };
  const newRecalc = sprRecalcRollRow(newRow);
  newRow.net_weight = newRecalc.net_weight;
  newRow.produced_gsm = newRecalc.produced_gsm;
  newRow.planned_qty = newRecalc.planned_qty;
  rollLines.value = sortRollLinesLifo([newRow, ...rollLines.value]);
  scheduleAutosave();
  } finally {
    addRollInProgress.value = false;
  }
}

async function previewNextBatch(ppId) {
  const gsmPrefix = _cstr(seriesPrefix.value || shiftBatchPrefix.value);
  const gsmMode = shiftOpened.value && !!gsmPrefix;
  syncBatchCounterFromGrid();
  const existing = allExistingBatchNos();
  const gsmBatchArgs = gsmMode
    ? { gsm_shift_prefix: 1, client_series_prefix: gsmPrefix }
    : {};
  const sprName = ppId ? sprNameForPp(ppId) : "";
  if (sprName) {
    try {
      const res = await frappe.call({
        method:
          "production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_next_spr_batch_numbers",
        args: {
          shaft_production_run: sprName,
          count: 1,
          client_max_roll: maxRollSuffix.value,
          run_date: runDate.value,
          custom_unit: headerUnit.value,
          shift: shift.value,
          client_series_prefix: gsmPrefix || undefined,
          existing_batches: JSON.stringify(existing),
          ...gsmBatchArgs,
        },
      });
      const row = (res.message || [])[0];
      if (row?.batch_no) {
        reserveBatchNo(row.batch_no, row.roll_no);
      }
      return row || { batch_no: "", roll_no: "" };
    } catch (e) {
      console.warn("get_next_spr_batch_numbers", e);
    }
  }
  const res = await frappe.call({
    method: "production_entry.production_planning.unified_production_entry_api.preview_spr_batch_numbers_for_entry",
    args: {
      unit: headerUnit.value,
      run_date: runDate.value,
      shift: shift.value,
      count: 1,
      client_max_roll: maxRollSuffix.value,
      client_series_prefix: gsmPrefix || undefined,
      existing_batches: JSON.stringify(existing),
      session_local: 1,
      ...gsmBatchArgs,
    },
  });
  const row = (res.message || [])[0];
  if (row?.batch_no) {
    reserveBatchNo(row.batch_no, row.roll_no);
  } else if (row?.series_prefix) {
    seriesPrefix.value = row.series_prefix;
  }
  return row || { batch_no: "", roll_no: "" };
}

function _removeRowBatchOptions() {
  const opts = [];
  const seen = new Set();
  for (const row of rollLines.value) {
    const bn = _cstr(row?.batch_no).trim();
    if (!bn || seen.has(bn)) continue;
    seen.add(bn);
    const order = _cstr(row.order_code || row.pp_id || "").trim();
    const label = order ? `${bn} (${order})` : bn;
    opts.push({ value: bn, label });
  }
  return opts;
}

function promptRemoveRowByBatch() {
  const options = _removeRowBatchOptions();
  if (!options.length) {
    frappe.msgprint(__("No batch numbers on the grid to remove."));
    return;
  }
  frappe.prompt(
    [
      {
        fieldtype: "Select",
        fieldname: "batch_choice",
        label: __("Batch No to delete"),
        options: options.map((o) => o.label).join("\n"),
        reqd: 1,
        default: options[0].label,
        description: __("Select the batch number of the row to remove (including middle rows)."),
      },
    ],
    (values) => {
      const byLabel = options.find((o) => o.label === values.batch_choice);
      const batchNo = _cstr(byLabel?.value || values?.batch_choice).trim();
      if (!batchNo) return;
      removeRowByBatchNo(batchNo);
    },
    __("Remove Row"),
    __("Delete")
  );
}

async function removeRowByBatchNo(batchNo) {
  const bn = _cstr(batchNo).trim();
  if (!bn) return;
  const idx = rollLines.value.findIndex((r) => _cstr(r?.batch_no).trim() === bn);
  if (idx < 0) {
    frappe.msgprint(__("Batch No {0} was not found on the grid.", [bn]));
    return;
  }
  const row = rollLines.value[idx];
  if (row.is_mix_roll_row) {
    await removeMixRollRow(row);
    return;
  }
  const sprName = sprNameForPp(row.pp_id);
  const wasSaved = !!(row.spr_item_name || (row.row_locked && row.batch_no));

  const doRemove = async () => {
    if (row.is_bundle_row && sprName && (row.batch_no || row.child_roll_batches?.length)) {
      saveStatus.value = "Deleting bundle from SPR…";
      try {
        await frappe.call({
          method: "production_entry.production_planning.unified_production_entry_api.delete_gsm_bundle_packaging",
          args: {
            spr_name: sprName,
            bundle_batch_no: row.batch_no || "",
            child_roll_batches: JSON.stringify(row.child_roll_batches || []),
          },
        });
      } catch (e) {
        console.error(e);
        saveStatus.value = "Delete failed";
        frappe.msgprint(__("Could not remove bundle from {0}.", [sprName]));
        return;
      }
    } else if (wasSaved && sprName && row.batch_no) {
      saveStatus.value = "Deleting from SPR…";
      try {
        await frappe.call({
          method: "production_entry.production_planning.unified_production_entry_api.delete_gsm_roll_line",
          args: {
            spr_name: sprName,
            batch_no: row.batch_no,
            row_name: row.spr_item_name || undefined,
          },
        });
      } catch (e) {
        console.error(e);
        saveStatus.value = "Delete failed";
        frappe.msgprint(__("Could not remove row from {0}.", [sprName]));
        return;
      }
    }
    releaseBatchNo(row.batch_no);
    const stillIdx = rollLines.value.findIndex((r) => _cstr(r?.batch_no).trim() === bn);
    if (stillIdx >= 0) {
      rollLines.value.splice(stillIdx, 1);
    }
    syncBatchCounterFromGrid();
    scheduleAutosave();
    saveStatus.value = wasSaved ? "Removed from SPR" : "Row removed";
    await loadJobBoard();
  };

  if ((wasSaved && sprName) || (row.is_bundle_row && row.child_roll_batches?.length)) {
    frappe.confirm(
      __("Remove batch {0} from the grid and delete it from {1}?", [bn, sprName]),
      () => {
        doRemove();
      }
    );
    return;
  }
  await doRemove();
}

async function loadShiftConsolidatedSummary() {
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_consolidated_summary",
      args: {
        run_date: shiftFilterDate.value,
        shift: shiftFilterShift.value,
        unit: shiftFilterUnit.value || undefined,
      },
    });
    shiftConsolidated.value = res.message || null;
  } catch (e) {
    console.error(e);
    shiftConsolidated.value = null;
  }
}

async function loadSummaryShiftSummary() {
  summaryShiftLoading.value = true;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_consolidated_summary",
      args: {
        run_date: summaryShiftDate.value,
        shift: summaryShiftShift.value,
        unit: summaryShiftUnit.value || undefined,
      },
    });
    summaryShiftSummary.value = res.message || null;
  } catch (e) {
    console.error(e);
    summaryShiftSummary.value = null;
    frappe.msgprint("Failed to load shift summary");
  } finally {
    summaryShiftLoading.value = false;
  }
}

async function loadShiftEntries() {
  shiftLoading.value = true;
  selectedShiftEntry.value = null;
  try {
    const [entriesRes] = await Promise.all([
      frappe.call({
        method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_submitted_entries",
        args: {
          run_date: shiftFilterDate.value,
          shift: shiftFilterShift.value,
          unit: shiftFilterUnit.value || undefined,
        },
      }),
      loadShiftConsolidatedSummary(),
    ]);
    shiftEntries.value = entriesRes.message || [];
    if (shiftEntries.value.length) {
      selectedShiftEntry.value = shiftEntries.value[0];
    }
  } catch (e) {
    console.error(e);
    frappe.msgprint("Failed to load shift entries");
  } finally {
    shiftLoading.value = false;
  }
}

const prevShiftLabel = computed(() => {
  if (shift.value === "Night Shift") {
    return `Day Shift · ${runDate.value}`;
  }
  const d = new Date(runDate.value);
  d.setDate(d.getDate() - 1);
  const prev = d.toISOString().slice(0, 10);
  return `Night Shift · ${prev}`;
});

function prevShiftParams() {
  if (shift.value === "Night Shift") {
    return { run_date: runDate.value, shift: "Day Shift" };
  }
  const d = new Date(runDate.value);
  d.setDate(d.getDate() - 1);
  const prev = d.toISOString().slice(0, 10);
  return { run_date: prev, shift: "Night Shift" };
}

function openPrevShiftTab() {
  pageTab.value = "prevshift";
  if (!prevShiftConsolidated.value) {
    loadPrevShiftConsolidated();
  }
}

async function loadPrevShiftConsolidated() {
  prevShiftLoading.value = true;
  try {
    const params = prevShiftParams();
    const r = await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.get_gsm_shift_consolidated_summary",
      args: {
        run_date: params.run_date,
        shift: params.shift,
        unit: headerUnit.value || undefined,
      },
    });
    prevShiftConsolidated.value = r.message || null;
  } catch (e) {
    console.error("prev shift load error", e);
    frappe.msgprint("Failed to load previous shift data.");
  } finally {
    prevShiftLoading.value = false;
  }
}

function persistDraft() {
  try {
    const payload = {
      runDate: runDate.value,
      shift: shift.value,
      headerUnit: headerUnit.value,
      operator: operator.value,
      supervisor: supervisor.value,
      coordinator: coordinator.value,
      filterUnit: filterUnit.value,
      filterDate: filterDate.value,
      seriesPrefix: seriesPrefix.value,
      maxRollSuffix: maxRollSuffix.value,
      creationSeq: creationSeq.value,
      batchContextKey: currentBatchContextKey(),
      sessionHeaderCollapsed: sessionHeaderCollapsed.value,
      dismissedMixSprs: dismissedMixSprs.value,
      activeMixSpr: activeMixRoll.value?.spr_name || persistedActiveMixSpr.value || "",
      activeMixSprUnit: headerUnit.value || filterUnit.value || "",
    };
    if (shiftOpened.value) {
      payload.selectedEntries = selectedEntries.value;
      payload.selectionLocked = selectionLocked.value;
    }
    if (!shiftOpened.value) {
      payload.rollLines = rollLines.value;
      payload.sessionSprs = sessionSprs.value;
    }
    localStorage.setItem(STORAGE_KEY.value, JSON.stringify(payload));
    if (!saveStatus.value || saveStatus.value === "Saved locally") {
      saveStatus.value = "Draft saved";
    }
  } catch (e) {
    saveStatus.value = "Save failed";
  }
}

function restoreDraft(options = {}) {
  const skipRollGrid = !!options.skipRollGrid;
  try {
    let raw = localStorage.getItem(STORAGE_KEY.value);
    if (!raw) {
      raw = localStorage.getItem("gsm_production_entry_draft_v2");
    }
    if (!raw) {
      return;
    }
    const d = JSON.parse(raw);
    if (d.runDate) {
      runDate.value = d.runDate;
    }
    if (d.shift) {
      shift.value = d.shift;
    }
    if (typeof d.sessionHeaderCollapsed === "boolean") {
      sessionHeaderCollapsed.value = d.sessionHeaderCollapsed;
    }
    if (Array.isArray(d.dismissedMixSprs)) {
      dismissedMixSprs.value = d.dismissedMixSprs.filter(Boolean);
    }
    if (d.activeMixSpr && !dismissedMixSprs.value.includes(d.activeMixSpr)) {
      const mixUnit = _cstr(d.activeMixSprUnit || d.headerUnit || d.filterUnit || "");
      const nowUnit = _cstr(headerUnit.value || filterUnit.value || d.headerUnit || "");
      if (!mixUnit || !nowUnit || mixUnit === nowUnit) {
        persistedActiveMixSpr.value = d.activeMixSpr;
      }
    }
    if (d.filterDate) {
      filterDate.value = d.filterDate;
    }
    const draftUnit = _cstr(d.headerUnit || d.filterUnit || "");
    const draftCtxKey =
      d.batchContextKey ||
      (draftUnit ? `${d.runDate || runDate.value}|${d.shift || shift.value}|${draftUnit}` : "");
    if (draftUnit && draftCtxKey) {
      headerUnit.value = draftUnit;
      filterUnit.value = d.filterUnit || draftUnit;
    }
    const ctxKey = currentBatchContextKey();
    const draftMatchesContext = Boolean(draftCtxKey && draftCtxKey === ctxKey);
    if (draftMatchesContext) {
      if (shiftOpened.value) {
        if (d.selectedEntries?.length) {
          selectedEntries.value = d.selectedEntries;
        } else if (d.selectedLineIds?.length) {
          const pd = d.filterDate || filterDate.value;
          selectedEntries.value = d.selectedLineIds.map((id) => ({
            key: entryKey(pd, id),
            lineId: id,
            plannedDate: pd,
            ppId: "",
            orderCode: "",
            quality: "",
            color: "",
            gsm: 0,
            width_inch: 0,
            widthLabel: "",
            dayTargetKg: 0,
            sourceSnapshot: {},
          }));
        } else {
          selectedEntries.value = [];
        }
        selectionLocked.value = !!d.selectionLocked;
      } else {
        selectedEntries.value = [];
        selectionLocked.value = false;
      }
      seriesPrefix.value = d.seriesPrefix || "";
      maxRollSuffix.value = d.maxRollSuffix || 0;
      if (!skipRollGrid && !shiftOpened.value) {
        rollLines.value = sortRollLinesLifo(
          (d.rollLines || []).map((r) => ({
            ...r,
            gross_weight: r.gross_weight != null && r.gross_weight !== "" ? String(r.gross_weight) : "",
            row_locked: !!r.row_locked,
            row_ready_for_print: !!r.row_ready_for_print,
          }))
        );
        sessionSprs.value = d.sessionSprs || {};
      }
    } else {
      selectedEntries.value = [];
      selectionLocked.value = false;
      headerUnit.value = "";
      filterUnit.value = "";
      if (!skipRollGrid) {
        rollLines.value = [];
        sessionSprs.value = {};
      }
      resetBatchSeriesCache();
    }
    batchContextKey.value = ctxKey;
    void backfillSessionSprLabelTypes();
    syncCreationSeqFromGrid();
    syncBatchCounterFromGrid();
    saveStatus.value = draftMatchesContext ? "Draft restored" : "Draft unit context reset";
  } catch (e) {
    console.warn("draft restore failed", e);
  }
}

function scheduleAutosave() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer);
  }
  autosaveTimer = setTimeout(() => {
    saveStatus.value = "Saving…";
    persistDraft();
    if (saveStatus.value === "Saving…") {
      saveStatus.value = "Draft saved";
    }
  }, 5000);
}

function scheduleJobSelectionSave() {
  if (!shiftOpened.value || !headerUnit.value) {
    return;
  }
  if (jobSelectionSaveTimer) {
    clearTimeout(jobSelectionSaveTimer);
  }
  jobSelectionSaveTimer = setTimeout(() => {
    persistJobSelectionToServer();
  }, 500);
}

async function persistJobSelectionToServer() {
  if (!shiftOpened.value || !runDate.value || !shift.value || !headerUnit.value) {
    return;
  }
  try {
    await frappe.call({
      method: "production_entry.production_planning.unified_production_entry_api.save_gsm_session_job_selections",
      args: {
        run_date: runDate.value,
        shift: shift.value,
        unit: headerUnit.value,
        entries: JSON.stringify(buildSessionEntries()),
        selection_locked: selectionLocked.value ? 1 : 0,
      },
    });
  } catch (e) {
    console.warn("job selection save", e);
  }
}

async function ensureSidebarForOpenShift() {
  if (!shiftOpened.value || !runDate.value) {
    return;
  }
  await fetchOrders();
  await loadJobBoard();
  enrichSelectedEntriesFromBoard();
  await loadMixRollCandidates();
}

watch([filterDate, filterUnit, filterWeek, filterMonth, viewScope, shiftOpened], () => {
  if (shiftOpened.value && (headerUnit.value || filterUnit.value)) {
    void loadMixRollCandidates();
  }
});

watch([shiftOpened, runDate], () => {
  if (shiftOpened.value && runDate.value) {
    ensureSidebarForOpenShift();
  }
});

watch([runDate, shift, headerUnit], () => {
  const key = currentBatchContextKey();
  if (gsmContextKeyChanged(batchContextKey.value, key)) {
    resetBatchSeriesCache();
    if (!shiftOpened.value) {
      clearGsmUnitContextState();
    }
  }
  batchContextKey.value = key;
  if (ppSubmittedRows.value.length) {
    loadJobBoard();
    loadQuotaForLines();
  }
  refreshShiftSession();
});

watch([runDate, shift, operator, supervisor, coordinator], () => scheduleAutosave());

onMounted(async () => {
  pageLoadError.value = false;
  setupGsmSessionWatch();
  try {
    await Promise.all([loadCurrentShift(), loadGsmBoardAccess()]);
    await loadCoreWidthOptions();
    shiftFilterDate.value = runDate.value;
    shiftFilterShift.value = shift.value;
    await fetchOrders();
    restoreDraft({ skipRollGrid: true });
    if (filterUnit.value) {
      headerUnit.value = filterUnit.value;
      shiftFilterUnit.value = filterUnit.value;
      batchContextKey.value = currentBatchContextKey();
      await refreshShiftSession();
      if (shiftOpened.value && shouldResumeFromServer()) {
        await refreshSessionFromServer({ merge: true });
      }
      if (shiftOpened.value) {
        setupGsmLiveSync();
        await loadMixRollCandidates();
      }
    } else {
      shiftFilterUnit.value = "";
      batchContextKey.value = currentBatchContextKey();
      shiftSessionReady.value = true;
    }
  } catch (e) {
    console.error("GSM page load error", e);
    pageLoadError.value = true;
  }
});

onUnmounted(() => {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer);
  }
  stopShiftReminderTimers();
  stopSubmitProgressTimer();
  teardownGsmLiveSync();
  teardownGsmSessionWatch();
});
</script>

<style scoped>
.gpe-root {
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  font-size: 17px;
  color: #0f172a;
  padding: 10px 12px;
  background: linear-gradient(160deg, #f1f5f9 0%, #e2e8f0 100%);
  min-height: 100vh;
  box-sizing: border-box;
  overflow-x: hidden;
  -webkit-font-smoothing: antialiased;
}
.gpe-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06);
}
.gpe-card-inner {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px;
  background: #f8fafc;
}
.gpe-info-strip {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1e40af;
  padding: 8px 12px;
  border-radius: 12px;
  margin-bottom: 12px;
  font-size: 12px;
}
.gpe-resume-banner {
  background: #ecfdf5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  padding: 8px 12px;
  border-radius: 10px;
  margin: 8px 0;
  font-size: 13px;
}
.gpe-shift-opened-by {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 8px;
}
.gpe-page-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
}
.gpe-page-tabs button {
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  font-weight: 500;
}
.gpe-page-tabs button.active {
  background: #4f46e5;
  color: #fff;
  border-color: #4f46e5;
}
.gpe-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  margin-bottom: 12px;
  padding: 12px;
}
.gpe-filter label {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 2px;
}
.gpe-filter input,
.gpe-filter select {
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
}
.gpe-unit-locked {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 8px;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  color: #334155;
  font-weight: 600;
  font-size: 13px;
}
.gpe-layout {
  display: grid;
  grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
  height: calc(100vh - 168px);
  min-height: 520px;
}
.gpe-layout-entry {
  position: relative;
}
.gpe-shift-not-started-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-size: 13px;
}
.gpe-shift-open-dialog {
  width: min(480px, 92vw);
}
.gpe-shift-reopen-fields {
  display: grid;
  gap: 10px;
}
.gpe-shift-reopen-fields label {
  display: block;
  font-size: 11px;
  color: #64748b;
}
.gpe-shift-reopen-fields select,
.gpe-shift-reopen-fields textarea {
  width: 100%;
  margin-top: 4px;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  box-sizing: border-box;
}
.gpe-reopen-notice {
  margin: 8px 0 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
  font-size: 12px;
}
.gpe-submit-confirm-dialog {
  width: min(1000px, 94vw);
  max-height: 88vh;
  overflow: auto;
}
.gpe-submit-summary-totals {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 10px 0 14px;
  font-size: 13px;
}
.gpe-submit-section-title {
  margin: 14px 0 6px;
  font-size: 13px;
  color: #334155;
}
.gpe-submit-spr-list {
  margin: 0 0 12px;
  padding-left: 18px;
  font-size: 12px;
}
.gpe-shift-open-fields {
  display: grid;
  gap: 12px;
  margin: 14px 0 16px;
}
.gpe-emp-link label {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 4px;
}
.gpe-emp-link-row {
  display: flex;
  gap: 8px;
}
.gpe-emp-link-row input {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
}
.gpe-shift-batch-preview {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.gpe-shift-batch-label {
  font-size: 11px;
  color: #64748b;
}
.gpe-batch-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  background: #eef2ff;
  color: #3730a3;
  font-size: 13px;
  letter-spacing: 0.02em;
}
.gpe-batch-badge-wrap {
  flex: 1;
}
.gpe-session-head-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.gpe-session-head-row .gpe-session-title {
  margin: 0;
}
.gpe-hide-session-btn {
  margin-left: auto;
}
.gpe-close-shift-btn {
  margin-left: 0;
}
.gpe-shift-status-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.gpe-shift-status-strip-compact {
  margin-top: -4px;
}
.gpe-shift-chip {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
}
.gpe-shift-chip.open {
  background: #ecfdf5;
  border-color: #6ee7b7;
  color: #047857;
}
.gpe-shift-chip.other-open {
  background: #fff7ed;
  border-color: #fdba74;
  color: #c2410c;
}
.gpe-shift-chip.closed {
  background: #f1f5f9;
  color: #64748b;
}
.gpe-shift-start-btn {
  width: 100%;
}
@media (max-width: 1100px) {
  .gpe-layout {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }
}
.gpe-sidebar {
  padding: 10px;
  max-height: none;
  height: 100%;
  overflow: auto;
}
.gpe-sidebar h3 {
  margin: 0 0 8px;
  font-size: 15px;
}
.gpe-session-panel {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
  border-radius: 10px;
  font-size: 12px;
}
.gpe-session-panel-head {
  font-weight: 700;
  color: #3730a3;
  margin-bottom: 8px;
  font-size: 13px;
}
.gpe-session-entry {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  padding: 6px 0;
  border-bottom: 1px solid #e0e7ff;
  align-items: center;
}
.gpe-session-entry:last-of-type {
  border-bottom: none;
}
.gpe-session-date {
  background: #dbeafe;
  color: #1e40af;
  padding: 2px 8px;
  border-radius: 6px;
  font-weight: 600;
  font-size: 11px;
}
.gpe-session-hint {
  margin: 8px 0 0;
  font-size: 11px;
  color: #64748b;
}
.gpe-sidebar-date-note {
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-size: 11px;
  padding: 6px 8px;
  border-radius: 8px;
  margin: 8px 0;
}
.gpe-hint {
  font-size: 11px;
  color: #64748b;
  margin: 0 0 10px;
}
.gpe-sidebar-section {
  margin-bottom: 10px;
}
.gpe-sidebar-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.gpe-mix-roll-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.gpe-mix-roll-card {
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 10px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.gpe-mix-roll-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 14px;
}
.gpe-mix-roll-head strong {
  font-size: 14px;
  color: #1e293b;
}
.gpe-mix-roll-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #475569;
  margin-bottom: 10px;
  line-height: 1.4;
}
.gpe-mix-counts {
  display: inline-block;
  margin-top: 2px;
  padding: 2px 8px;
  border-radius: 6px;
  background: #eef2ff;
  color: #3730a3;
  font-weight: 700;
  font-size: 12px;
}
.gpe-mix-active-banner {
  margin-top: 8px;
  padding: 8px;
  background: #eef2ff;
  border-radius: 8px;
  font-size: 12px;
}
.gpe-mix-roll-workspace {
  margin-bottom: 6px;
  padding: 6px 8px;
  border-left: 4px solid #7c3aed;
}
.gpe-mix-roll-workspace-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.gpe-mix-roll-title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
  min-width: 0;
}
.gpe-mix-roll-title strong {
  color: #0f172a;
  font-size: 14px;
  font-weight: 800;
}
.gpe-mix-roll-title span,
.gpe-mix-roll-title a {
  font-size: 10px;
}
.gpe-mix-grid-wrap {
  max-height: 140px;
  overflow: auto;
}
.gpe-mix-grid-wrap .gpe-grid th,
.gpe-mix-grid-wrap .gpe-grid td {
  padding-top: 6px;
  padding-bottom: 6px;
  font-size: 17px;
  font-weight: 600;
}
.gpe-mix-grid-wrap .gpe-inp {
  min-height: 40px;
  font-size: 17px;
  padding-top: 4px;
  padding-bottom: 4px;
}
.gpe-mix-roll-workspace-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.gpe-row-mix {
  box-shadow: inset 4px 0 0 #7c3aed;
}
.gpe-btn-sm {
  padding: 4px 10px;
  font-size: 12px;
}
.gpe-section-title {
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.gpe-collapse-btn {
  border: none;
  background: none;
  color: #4f46e5;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
  margin-bottom: 8px;
}
.gpe-order-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 12px;
  margin-bottom: 12px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
}
.gpe-order-code {
  font-size: 19px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.02em;
}
.gpe-order-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid #f1f5f9;
}
.gpe-order-target,
.gpe-job-target {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  font-size: 11px;
  width: 100%;
}
.gpe-order-target {
  padding: 0 2px 4px;
  margin-bottom: 6px;
}
.gpe-day-target {
  color: #1d4ed8;
  font-weight: 600;
}
.gpe-day-rem {
  color: #b45309;
  font-weight: 600;
}
.gpe-job-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  margin-bottom: 6px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  overflow: hidden;
}
.gpe-job-card:hover {
  border-color: #93c5fd;
  background: #f8fafc;
}
.gpe-job-card.selected {
  border-color: #3b82f6;
  background: #eff6ff;
}
.gpe-job-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}
.gpe-job-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  line-height: 1.3;
}
.gpe-job-head-dot {
  color: #94a3b8;
  font-weight: 700;
  font-size: 14px;
}
.gpe-job-title {
  font-weight: 800;
  font-size: 17px;
  color: #0f172a;
  letter-spacing: -0.01em;
}
.gpe-job-gsm {
  font-weight: 800;
  font-size: 14px;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 5px;
  padding: 2px 8px;
  white-space: nowrap;
  line-height: 1.35;
}
.gpe-job-spec,
.gpe-order-spec {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  min-width: 0;
}
.gpe-order-spec {
  margin: 0 0 8px;
}
.gpe-spec-chip {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 5px;
  line-height: 1.35;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.gpe-spec-quality {
  color: #334155;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
}
.gpe-spec-color {
  color: #065f46;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
}
.gpe-spec-manual {
  color: #9a3412;
  background: #fff7ed;
  border: 1px solid #fdba74;
}
.gpe-job-combination {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.4;
  padding: 7px 9px;
  background: #f1f5f9;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  word-break: break-word;
  overflow-wrap: anywhere;
  text-align: left;
  width: 100%;
  box-sizing: border-box;
}
.gpe-job-card.selected .gpe-job-combination {
  background: #dbeafe;
  border-color: #bfdbfe;
}
.gpe-dual-meter {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 8px;
  background: #f1f5f9;
  border-radius: 8px;
  width: 100%;
  box-sizing: border-box;
}
.gpe-dual-meter-full {
  background: #fef3c7;
}
.gpe-dual-meter-done {
  background: #ecfdf5;
}
.gpe-meter-col {
  text-align: center;
}
.gpe-meter-label {
  display: block;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #64748b;
  margin-bottom: 2px;
}
.gpe-meter-frac {
  font-size: 16px;
  font-weight: 600;
}
.gpe-meter-frac em {
  font-style: normal;
  color: #2563eb;
}
.gpe-meter-frac span {
  margin: 0 2px;
  color: #94a3b8;
}
.gpe-meter-frac strong {
  color: #0f172a;
}
.gpe-meter-context {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  width: 100%;
  text-align: left;
}
.gpe-shift-breakdown {
  font-size: 13px;
  font-weight: 700;
  color: #1e293b;
  line-height: 1.45;
  width: 100%;
  text-align: left;
}
.gpe-shift-today {
  font-weight: 800;
  color: #0f172a;
}
.gpe-shift-part {
  font-weight: 700;
}
.gpe-shift-other {
  color: #c2410c;
}
.gpe-shift-other strong {
  color: #9a3412;
  font-weight: 800;
}
.gpe-shift-current {
  color: #2563eb;
  font-weight: 800;
}
.gpe-shift-current strong {
  color: #1d4ed8;
  font-weight: 800;
}
.gpe-job-remaining {
  font-size: 14px;
  color: #b45309;
  font-weight: 700;
  padding: 6px 8px;
  background: #fff7ed;
  border-radius: 6px;
  line-height: 1.35;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
}
.gpe-job-foot {
  width: 100%;
}
.gpe-picker-sub {
  display: block;
  font-size: 12px;
  color: #475569;
  font-style: normal;
  font-weight: 600;
  margin-top: 4px;
}
.gpe-picker-maxed-tag {
  color: #b45309;
  font-weight: 800;
}
.gpe-picker-manual-tag {
  color: #7c3aed;
  font-weight: 800;
}
.gpe-picker-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.gpe-picker-maxed {
  background: #fffbeb;
  border-color: #fcd34d;
}
.gpe-picker-maxed-note {
  background: #fef3c7;
  border: 1px solid #fcd34d;
  color: #92400e;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 12px;
}
.gpe-btn.warn {
  border-color: #f59e0b;
  color: #92400e;
  background: #fffbeb;
}
.gpe-btn.warn:hover:not(:disabled) {
  background: #fef3c7;
}
.gpe-line-card {
  display: flex;
  gap: 12px;
  padding: 12px;
  border-radius: 12px;
  cursor: pointer;
  border: 1px solid #e2e8f0;
  margin-bottom: 8px;
  align-items: flex-start;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.gpe-line-card:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
}
.gpe-line-card.selected {
  background: linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%);
  border-color: #818cf8;
  box-shadow: 0 0 0 1px #c7d2fe;
}
.gpe-line-check {
  margin-top: 4px;
  flex-shrink: 0;
}
.gpe-line-body {
  flex: 1;
  min-width: 0;
}
.gpe-line-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.gpe-quality {
  color: #1e3a8a;
}
.gpe-color {
  color: #6b21a8;
}
.gpe-line-spec {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  margin-top: 6px;
  font-size: 14px;
  font-weight: 500;
  color: #475569;
}
.gpe-gsm {
  font-size: 18px;
  font-weight: 800;
  color: #312e81;
  letter-spacing: -0.02em;
}
.gpe-day-target {
  color: #0f766e;
  font-weight: 700;
  font-size: 14px;
}
.gpe-day-rem {
  color: #c2410c;
  font-weight: 700;
  font-size: 14px;
}
.gpe-line-foot {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}
.gpe-roll-meter {
  border-radius: 10px;
  padding: 10px 12px;
  background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%);
  border: 1px solid #fdba74;
}
.gpe-roll-meter-full {
  background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
  border-color: #fca5a5;
}
.gpe-roll-meter-done {
  opacity: 0.85;
}
.gpe-roll-meter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.gpe-roll-meter-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #9a3412;
}
.gpe-roll-meter-frac {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 22px;
  font-weight: 800;
  color: #7c2d12;
  line-height: 1;
}
.gpe-roll-meter-frac em {
  font-style: normal;
  font-size: 26px;
  color: #c2410c;
}
.gpe-roll-meter-frac span {
  font-weight: 600;
  color: #9a3412;
  font-size: 18px;
}
.gpe-roll-meter-frac strong {
  font-size: 22px;
  color: #431407;
}
.gpe-roll-meter-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed rgba(154, 52, 18, 0.25);
  font-size: 12px;
  font-weight: 600;
}
.gpe-roll-prior {
  background: #fff;
  color: #1d4ed8;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid #bfdbfe;
}
.gpe-roll-day {
  color: #64748b;
  font-weight: 700;
}
.gpe-selection-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding: 14px 16px;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
  border-radius: 12px;
}
.gpe-selection-strip-lg {
  padding: 16px 18px;
}
.gpe-selection-text {
  font-size: 15px;
  line-height: 1.5;
}
.gpe-session-title {
  margin: 0 0 10px;
  font-size: 18px;
  font-weight: 700;
  color: #1e293b;
}
.gpe-session-panel-main {
  padding: 16px 18px !important;
}
.gpe-header-fields-lg label {
  font-size: 13px;
  font-weight: 600;
}
.gpe-header-fields-lg input,
.gpe-header-fields-lg select {
  min-height: 36px;
  font-size: 14px;
  padding: 8px 10px;
}
.gpe-spr-table-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}
.gpe-spr-table-wrap {
  margin-top: 12px;
  overflow-x: auto;
}
.gpe-spr-table {
  width: 100%;
  max-width: 640px;
  border-collapse: collapse;
  font-size: 14px;
}
.gpe-spr-table th,
.gpe-spr-table td {
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  text-align: left;
}
.gpe-spr-table th {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
}
.gpe-shaft-block {
  margin-bottom: 16px;
}
.gpe-shaft-block-head {
  margin-bottom: 8px;
  font-size: 14px;
}
.gpe-shaft-grid {
  font-size: 13px;
}
.gpe-lock-badge.inline {
  margin-left: 8px;
  display: inline-block;
  margin-top: 0;
}
.gpe-gsm-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
}
.gpe-legend-title {
  font-weight: 600;
  color: #475569;
}
.gpe-legend-chip {
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  font-weight: 600;
}
.gpe-shift-filter-title {
  font-weight: 600;
  color: #334155;
  margin-right: 8px;
}
.gpe-shift-view-toggle {
  display: flex;
  gap: 4px;
}
.gpe-shift-view-toggle button {
  padding: 6px 10px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 8px;
  font-size: 11px;
  cursor: pointer;
}
.gpe-shift-view-toggle button.active {
  background: #4f46e5;
  color: #fff;
  border-color: #4f46e5;
}
.gpe-shift-kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  padding: 12px;
  margin-bottom: 12px;
}
.gpe-shift-kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px 16px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.gpe-shift-summary-cards .gpe-panel {
  padding: 14px 16px;
  overflow: visible;
  min-height: 120px;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
}
.gpe-shift-summary-cards .gpe-panel h4 {
  margin: 0 0 10px;
  line-height: 1.35;
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  padding-bottom: 6px;
  border-bottom: 1px solid #e2e8f0;
}
.gpe-shift-summary-cards .gpe-table-wrap,
.gpe-shift-summary-cards table {
  width: 100%;
}
.gpe-shift-summary-cards table {
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}
.gpe-shift-summary-cards table th,
.gpe-shift-summary-cards table td {
  border-bottom: 1px solid #e2e8f0;
  border-right: 1px solid #f1f5f9;
  padding: 8px 10px;
  text-align: left;
}
.gpe-shift-summary-cards table th:last-child,
.gpe-shift-summary-cards table td:last-child {
  border-right: none;
}
.gpe-shift-summary-cards table thead th {
  background: #f1f5f9;
  font-weight: 700;
  color: #475569;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.gpe-shift-summary-cards table tbody tr:hover {
  background: #f8fafc;
}
.gpe-panel.wide.gpe-card h4,
.gpe-panel.wide.gpe-card-inner h4 {
  margin: 0 0 10px;
  line-height: 1.35;
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  padding-bottom: 6px;
  border-bottom: 1px solid #e2e8f0;
}
.gpe-panel.wide .gpe-table-wrap table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}
.gpe-panel.wide .gpe-table-wrap table th,
.gpe-panel.wide .gpe-table-wrap table td {
  border-bottom: 1px solid #e2e8f0;
  border-right: 1px solid #f1f5f9;
  padding: 8px 10px;
  text-align: left;
}
.gpe-panel.wide .gpe-table-wrap table th:last-child,
.gpe-panel.wide .gpe-table-wrap table td:last-child {
  border-right: none;
}
.gpe-panel.wide .gpe-table-wrap table thead th {
  background: #f1f5f9;
  font-weight: 700;
  color: #475569;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.gpe-panel.wide .gpe-table-wrap table tbody tr:hover {
  background: #f8fafc;
}
.gpe-panel.wide .gpe-table-wrap table td:nth-child(4),
.gpe-panel.wide .gpe-table-wrap table td:nth-child(5) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gpe-shift-summary-cards table td:nth-child(n+3) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gpe-shift-kpi-grid.gpe-card-inner,
.gpe-shift-kpi-grid.gpe-card {
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
}
.gpe-kpi.gpe-board-card {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 12px;
  background: #f8fafc;
}
.gpe-shift-roll-section {
  margin-top: 12px;
  padding: 12px 14px;
}
.gpe-shift-roll-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.gpe-shift-roll-head h4 {
  margin: 0;
  font-size: 14px;
  line-height: 1.35;
}
.gpe-roll-count-badge {
  padding: 3px 10px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 11px;
  font-weight: 700;
}
.gpe-trunc-hint {
  font-size: 11px;
}
.gpe-grid-wrap-shift {
  max-height: min(52vh, 520px);
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.gpe-grid-readonly td,
.gpe-grid-readonly th {
  white-space: nowrap;
}
.gpe-submit-progress {
  text-align: center;
  padding: 24px 12px;
}
.gpe-submit-spinner {
  width: 40px;
  height: 40px;
  margin: 0 auto 16px;
  border: 3px solid #e2e8f0;
  border-top-color: #4f46e5;
  border-radius: 50%;
  animation: gpe-spin 0.9s linear infinite;
}
@keyframes gpe-spin {
  to { transform: rotate(360deg); }
}
.gpe-submit-progress-msg {
  font-size: 14px;
  font-weight: 600;
  color: #334155;
  margin: 0 0 8px;
}
.gpe-submit-success-title {
  color: #166534;
  margin-bottom: 8px;
}
.gpe-submit-success-msg {
  font-size: 13px;
  color: #334155;
}
.gpe-submit-error-msg {
  color: #991b1b;
  font-size: 13px;
}
.gpe-kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 14px;
}
.gpe-kpi-label {
  font-size: 11px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 700;
}
.gpe-kpi-sub {
  font-size: 12px;
  color: #64748b;
}
.gpe-shift-consolidated {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.gpe-shift-summary-panel {
  margin-top: 10px;
}
.gpe-clickable-row {
  cursor: pointer;
}
.gpe-clickable-row:hover {
  background: #f8fafc;
}
.gpe-inp-wide {
  width: 160px;
  max-width: 180px;
}
.gpe-order-group {
  margin-top: 8px;
}
.gpe-party {
  display: none;
}
.gpe-line {
  display: flex;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  align-items: flex-start;
}
.gpe-line.selected {
  background: #eef2ff;
  font-weight: 600;
}
.gpe-line-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.gpe-completed .gpe-order-head {
  opacity: 0.7;
}
.gpe-merge-label {
  font-weight: 600;
  color: #7c3aed;
}
.gpe-quota {
  color: #b45309;
  font-size: 11px;
}
.gpe-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  align-self: flex-start;
}
.gpe-chip-draft {
  background: #fef3c7;
  color: #92400e;
}
.gpe-chip-done {
  background: #f1f5f9;
  color: #64748b;
}
.gpe-chip-closed {
  background: #fee2e2;
  color: #991b1b;
}
.gpe-chip-quota {
  background: #ffedd5;
  color: #c2410c;
}
.gpe-chip-submitted {
  background: #dcfce7;
  color: #166534;
}
.gpe-chip-mix {
  background: #ede9fe;
  color: #5b21b6;
  margin-left: 6px;
  font-size: 10px;
  padding: 2px 7px;
}
.gpe-chip-merge {
  background: #ede9fe;
  color: #6d28d9;
}
.gpe-selected-box {
  margin-top: 12px;
  padding: 10px;
  background: #f1f5f9;
  border-radius: 10px;
  font-size: 12px;
}
.gpe-selection-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
  flex-wrap: wrap;
}
.gpe-lock-badge {
  margin-top: 6px;
  font-size: 11px;
  color: #4f46e5;
  font-weight: 600;
}
.gpe-main {
  padding: 8px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.gpe-entry-workspace {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.gpe-session-panel-main {
  flex-shrink: 0;
}
.gpe-header.gpe-session-panel-main {
  padding: 8px;
  margin-bottom: 6px;
}
.gpe-header-session-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px 16px;
  margin-top: 6px;
  position: relative;
  z-index: 4;
}
.gpe-spr-inline {
  flex: 1 1 320px;
  min-width: 280px;
  margin-top: 0 !important;
}
.gpe-spr-ready-badge {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  border-radius: 8px;
  background: #dcfce7;
  color: #166534;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid #86efac;
}
.gpe-metrics-compact {
  margin: 4px 0 !important;
  flex-shrink: 0;
}
.gpe-metrics-compact .gpe-metric {
  padding: 8px 12px;
  font-size: 15px;
}
.gpe-metrics-compact .gpe-metric strong {
  font-size: 24px;
}
.gpe-gsm-legend {
  flex-shrink: 0;
  margin: 2px 0 4px;
  font-size: 13px;
}
.gpe-header-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  font-size: 12px;
}
.gpe-header-fields label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.gpe-header-fields input,
.gpe-header-fields select {
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
}
.gpe-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.gpe-tag {
  background: #e0e7ff;
  color: #3730a3;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
}
.gpe-spr-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
}
.gpe-spr-table {
  width: 100%;
  max-width: 520px;
  border-collapse: collapse;
  font-size: 12px;
}
.gpe-spr-table th,
.gpe-spr-table td {
  border: 1px solid #e2e8f0;
  padding: 6px 10px;
  text-align: left;
}
.gpe-spr-table th {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
}
.gpe-metrics {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  margin: 12px 0;
}
@media (max-width: 1100px) {
  .gpe-metrics {
    grid-template-columns: repeat(2, 1fr);
  }
}
.gpe-metric {
  padding: 12px;
  border-radius: 12px;
  text-align: center;
  font-size: 11px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  min-width: 0;
  overflow: hidden;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.gpe-metric strong {
  font-size: 20px;
  display: block;
  margin-top: 4px;
  line-height: 1.1;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.gpe-metric.slate { background: #e2e8f0; color: #1e293b; }
.gpe-metric.blue { background: #93c5fd; color: #1e3a8a; }
.gpe-metric.green { background: #86efac; color: #14532d; }
.gpe-metric.orange { background: #fdba74; color: #9a3412; }
.gpe-metric.grey { background: #cbd5e1; color: #334155; }
.gpe-load-error {
  text-align: center;
  padding: 40px 20px;
  margin: 20px 0;
  border: 1px solid #fed7d7;
  background: #fff5f5;
  border-radius: 8px;
}
.gpe-session-lock {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(15, 23, 42, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.gpe-session-lock-card {
  max-width: 440px;
  width: 100%;
  background: #fff;
  border-radius: 14px;
  padding: 22px 24px;
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
  color: #0f172a;
}
.gpe-session-lock-card h3 {
  margin: 0 0 10px;
  font-size: 20px;
  color: #991b1b;
}
.gpe-session-lock-card p {
  margin: 0 0 16px;
  font-size: 15px;
  line-height: 1.5;
  color: #334155;
}
.gpe-session-expired {
  margin: 8px 0 12px;
  padding: 12px 14px;
  border: 1px solid #f87171;
  background: #fef2f2;
  border-radius: 8px;
  color: #7f1d1d;
}
.gpe-session-expired strong {
  display: block;
  font-size: 15px;
  margin-bottom: 6px;
}
.gpe-session-expired p {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.45;
}
.gpe-load-error h3 {
  color: #c53030;
  margin-bottom: 8px;
}
.gpe-load-error p {
  color: #742a2a;
  margin-bottom: 16px;
}
.gpe-prev-shift-badge {
  font-size: 13px;
  font-weight: 600;
  color: #4a5568;
  background: #edf2f7;
  padding: 4px 10px;
  border-radius: 4px;
}
.gpe-create-spr-row {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  padding: 6px 0;
  margin-bottom: 2px;
}
.gpe-create-spr-row-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gpe-btn-create-spr {
  font-weight: 700;
  font-size: 14px;
  padding: 7px 18px;
}
.gpe-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 4px 0;
  flex-shrink: 0;
}
.gpe-toolbar-left {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.gpe-toolbar-right {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.gpe-btn {
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
}
.gpe-btn.sm {
  padding: 3px 8px;
  font-size: 11px;
}
.gpe-btn.primary {
  background: #4f46e5;
  color: #fff;
  border-color: #4f46e5;
}
.gpe-btn-warn {
  border-color: #f59e0b;
  color: #92400e;
  background: #fffbeb;
}
.gpe-btn-warn:hover:not(:disabled) {
  background: #fef3c7;
}
.gpe-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.gpe-link-btn {
  border: none;
  background: none;
  color: #4f46e5;
  cursor: pointer;
  font-size: 11px;
  text-decoration: underline;
}
.gpe-save-status {
  font-size: 11px;
  color: #64748b;
}
.gpe-tools-wrap {
  position: relative;
}
.gpe-tools-menu {
  position: absolute;
  right: 0;
  top: 100%;
  margin-top: 4px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
  z-index: 20;
  min-width: 200px;
  overflow: hidden;
}
.gpe-tools-menu button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border: none;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
}
.gpe-tools-menu button:hover {
  background: #f8fafc;
}
.gpe-grid-wrap-entry {
  overflow: auto;
  flex: 1 1 auto;
  min-height: 200px;
  max-height: none;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  box-shadow: inset -8px 0 12px -8px rgba(15, 23, 42, 0.12);
  -webkit-overflow-scrolling: touch;
}
.gpe-session-collapsed .gpe-grid-wrap-entry {
  min-height: 68vh;
}
.gpe-grid-entry {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  font-size: 19px;
}
.gpe-grid-entry th,
.gpe-grid-entry td {
  border-bottom: 1px solid #f1f5f9;
  padding: 10px 12px;
  white-space: nowrap;
}
.gpe-grid-entry tbody td {
  font-size: 19px;
  font-weight: 600;
  vertical-align: middle;
}
.gpe-grid-entry td.gpe-num,
.gpe-grid-entry th.gpe-num {
  text-align: center;
}
.gpe-grid-entry th {
  background: #f8fafc;
  position: sticky;
  top: 0;
  z-index: 2;
  font-size: 17px;
  font-weight: 800;
}
.gpe-sticky-col {
  position: sticky;
  z-index: 3;
  background: inherit;
}
.gpe-sticky-0 {
  left: 0;
  min-width: 36px;
}
.gpe-sticky-1 {
  left: 36px;
  min-width: 88px;
  box-shadow: 2px 0 4px -2px rgba(15, 23, 42, 0.08);
}
.gpe-grid-entry thead .gpe-sticky-col {
  background: #f8fafc;
  z-index: 4;
}
.gpe-len-cell {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
}
.gpe-inp-len {
  width: 72px;
}
.gpe-unit-suffix {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  line-height: 1;
}
.gpe-btn-label,
.gpe-btn-qc {
  font-size: 12px;
  padding: 6px 8px;
  white-space: nowrap;
}
.gpe-grid-wrap {
  overflow: auto;
  max-height: 420px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.gpe-grid-wrap.gpe-grid-wrap-entry {
  max-height: none;
  min-height: 200px;
  flex: 1 1 auto;
}
.gpe-grid {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.gpe-grid th,
.gpe-grid td {
  border-bottom: 1px solid #f1f5f9;
  padding: 6px 8px;
  white-space: nowrap;
}
.gpe-grid th {
  background: #f8fafc;
  position: sticky;
  top: 0;
  z-index: 1;
}
.gpe-grid tbody tr:nth-child(even) {
  background: #fafbfc;
}
.gpe-row-locked {
  background: #f0fdf4 !important;
}
.gpe-inp {
  width: 92px;
  min-height: 46px;
  padding: 9px 11px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font-size: 18px;
}
.gpe-grid-entry .gpe-inp {
  min-height: 46px;
  font-size: 18px;
}
.gpe-grid-entry .gpe-bay-select {
  width: 118px;
  min-width: 100px;
  max-width: 160px;
  min-height: 46px;
  font-size: 14px;
}
.gpe-grid-entry .gpe-btn.sm {
  font-size: 13px;
  padding: 8px 12px;
}
.gpe-inp:disabled {
  background: #f1f5f9;
  color: #64748b;
}
.gpe-actions {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  min-width: 0;
  white-space: nowrap;
  vertical-align: middle;
}
.gpe-grid-entry td.gpe-actions .gpe-btn,
.gpe-grid-entry td.gpe-actions .gpe-btn.sm {
  font-size: 12px;
  padding: 6px 8px;
  min-height: 32px;
  white-space: nowrap;
}
.gpe-wo-link {
  color: #4f46e5;
  text-decoration: none;
  font-size: 14px;
}
.gpe-gsm-band-0 { background: #6ee7b7 !important; color: #064e3b; }
.gpe-gsm-band-1 { background: #fde047 !important; color: #713f12; }
.gpe-gsm-band-2 { background: #fdba74 !important; color: #9a3412; }
.gpe-gsm-band-3 { background: #fca5a5 !important; color: #7f1d1d; }
.gpe-gsm-incomplete { background: #e2e8f0 !important; color: #475569; }
.gpe-summary-tab {
  margin-top: 0;
}
.gpe-tabs button {
  padding: 6px 12px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  cursor: pointer;
  border-radius: 8px 8px 0 0;
}
.gpe-tabs button.active {
  background: #4f46e5;
  color: #fff;
  border-color: #4f46e5;
}
.gpe-summary-panels {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-top: 10px;
}
@media (max-width: 1200px) {
  .gpe-summary-panels {
    grid-template-columns: 1fr;
  }
}
.gpe-panel {
  overflow: auto;
}
.gpe-panel.wide {
  grid-column: 1 / -1;
}
.gpe-panel h4 {
  margin: 0 0 6px;
  font-size: 12px;
}
.gpe-panel table {
  width: 100%;
  font-size: 11px;
  border-collapse: collapse;
}
.gpe-panel th,
.gpe-panel td {
  padding: 3px 4px;
  border-bottom: 1px solid #f1f5f9;
  text-align: right;
}
.gpe-panel th:first-child,
.gpe-panel td:first-child,
.gpe-panel th:nth-child(2),
.gpe-panel td:nth-child(2) {
  text-align: left;
}
.gpe-panel tr.total {
  font-weight: 700;
}
.gpe-progress {
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
  display: inline-block;
  width: 60px;
  vertical-align: middle;
}
.gpe-progress div {
  height: 100%;
  background: #3b82f6;
}
.gpe-note {
  font-size: 11px;
  color: #64748b;
  margin-top: 8px;
}
.gpe-muted {
  color: #94a3b8;
  font-size: 12px;
  padding: 16px;
}
.gpe-shift-layout {
  margin-top: 0;
}
.gpe-shift-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  padding: 12px;
  margin-bottom: 12px;
}
.gpe-shift-filters label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 11px;
  color: #64748b;
}
.gpe-shift-split {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 12px;
  min-height: 400px;
}
@media (max-width: 1000px) {
  .gpe-shift-split {
    grid-template-columns: 1fr;
  }
}
.gpe-shift-sidebar {
  padding: 8px;
  max-height: 520px;
  overflow: auto;
}
.gpe-shift-card {
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  margin-bottom: 8px;
  cursor: pointer;
  background: #fff;
}
.gpe-shift-card.active {
  border-color: #4f46e5;
  background: #eef2ff;
}
.gpe-shift-card-title {
  font-weight: 700;
  font-size: 12px;
}
.gpe-shift-card-meta {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
}
.gpe-shift-card-stats {
  font-size: 11px;
  margin-top: 4px;
}
.gpe-shift-detail {
  padding: 12px;
}
.gpe-shift-detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gpe-shift-meta {
  font-size: 12px;
  color: #64748b;
}
.gpe-wo-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}
.gpe-empty-state {
  padding: 32px;
  text-align: center;
}
.gpe-empty-state h3 {
  margin: 0 0 8px;
}
.gpe-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.gpe-dialog {
  width: min(480px, 92vw);
  padding: 16px;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.gpe-dialog h3 {
  margin: 0 0 12px;
  font-size: 17px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.01em;
}
.gpe-add-roll-wizard {
  width: min(560px, 94vw);
}
.gpe-confirm-list {
  margin: 12px 0;
  padding-left: 18px;
  font-size: 12px;
}
.gpe-confirm-grid {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin: 12px 0;
}
.gpe-confirm-grid th,
.gpe-confirm-grid td {
  border: 1px solid #e2e8f0;
  padding: 8px 10px;
  text-align: left;
}
.gpe-confirm-grid th {
  background: #f8fafc;
}
.gpe-dialog-wide {
  width: min(720px, 94vw);
  max-height: 85vh;
  overflow: auto;
}
.gpe-add-mix-roll-dialog {
  width: min(640px, 94vw);
}
.gpe-add-mix-roll-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 14px;
  margin: 14px 0 16px;
}
.gpe-add-mix-roll-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: #64748b;
  font-weight: 700;
}
.gpe-add-mix-roll-grid input,
.gpe-add-mix-roll-grid select {
  padding: 7px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
}
.gpe-add-mix-roll-grid input[readonly] {
  background: #f8fafc;
  color: #334155;
}
.gpe-add-mix-roll-span2 {
  grid-column: 1 / -1;
}
.gpe-add-mix-roll-hint {
  font-weight: 600;
  font-size: 11px;
}
@media (max-width: 560px) {
  .gpe-add-mix-roll-grid {
    grid-template-columns: 1fr;
  }
}
.gpe-spr-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0 0;
  font-size: 12px;
}
.gpe-spr-chip {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  padding: 4px 8px;
}
.gpe-label-type {
  margin-left: 6px;
  color: #64748b;
  font-size: 11px;
}
.gpe-tol-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  margin: 12px 0;
}
.gpe-tol-card-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
}
.gpe-tol-table {
  font-size: 12px;
  margin-bottom: 8px;
}
.gpe-tol-reason {
  display: block;
  font-size: 12px;
  margin-bottom: 8px;
}
.gpe-tol-reason textarea {
  width: 100%;
  margin-top: 4px;
}
.gpe-tol-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.gpe-picker-list {
  margin: 12px 0;
}
.gpe-picker-row {
  display: flex;
  gap: 10px;
  padding: 10px 10px;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  align-items: flex-start;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  margin-bottom: 6px;
  cursor: pointer;
  line-height: 1.35;
}
.gpe-picker-row:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}
.gpe-picker-row input[type="radio"] {
  margin-top: 3px;
  flex-shrink: 0;
}
.gpe-picker-row > span {
  font-weight: 700;
}
.gpe-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.gpe-wastage-recycle-btns {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.gpe-quality-check-wrap {
  position: relative;
}
.gpe-quality-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 400;
  min-width: 220px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
  overflow: hidden;
}
.gpe-quality-menu button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border: none;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
}
.gpe-quality-menu button:hover {
  background: #f8fafc;
}
.gpe-row-wasted td {
  text-decoration: line-through;
  color: #94a3b8;
  background: repeating-linear-gradient(
    -45deg,
    #f8fafc,
    #f8fafc 6px,
    #f1f5f9 6px,
    #f1f5f9 12px
  );
  opacity: 0.85;
}
.gpe-board-animate .gpe-board-card {
  animation: gpeBoardFadeIn 0.45s ease both;
  animation-delay: var(--gpe-delay, 0ms);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.gpe-board-animate .gpe-board-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}
@keyframes gpeBoardFadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
