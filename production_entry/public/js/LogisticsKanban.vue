<template>
  <div
    class="lk-container"
    :class="{
      'lk-mode-transfer': mode === 'transfer',
      'lk-mode-despatch': mode === 'despatch',
      'lk-mounted': mounted,
      'lk-gate-open': gateOpen,
    }"
  >
    <div class="lk-gate lk-gate-left" aria-hidden="true"><span class="lk-gate-label">IN</span></div>
    <div class="lk-gate lk-gate-right" aria-hidden="true"><span class="lk-gate-label">OUT</span></div>

    <div class="lk-hero">
      <div class="lk-hero-text">
        <h2 class="lk-title">Logistics</h2>
        <p class="lk-subtitle">Inter-company transfers and despatch lanes</p>
      </div>

      <div v-if="mode === 'transfer'" class="lk-truck-scene lk-scene-transfer" aria-hidden="true">
        <span class="lk-site lk-site-a">🏭</span>
        <div class="lk-road-transfer">
          <div class="lk-truck lk-truck-transfer">🚛</div>
        </div>
        <span class="lk-site lk-site-b">🏢</span>
      </div>
      <div v-else class="lk-truck-scene lk-scene-despatch" aria-hidden="true">
        <div class="lk-road-despatch">
          <div class="lk-truck lk-truck-despatch">🚛</div>
        </div>
        <span class="lk-customer">📦</span>
      </div>

      <div class="lk-toggle">
        <button type="button" :class="{ active: mode === 'transfer' }" :style="boardActionFrozenStyle(lkBoardAccess, 'logistics_transfer')" @click="guardedSetMode('transfer')">Transfer</button>
        <button type="button" :class="{ active: mode === 'despatch' }" :style="boardActionFrozenStyle(lkBoardAccess, 'logistics_despatch')" @click="guardedSetMode('despatch')">Despatch</button>
      </div>
    </div>

    <div class="lk-filters" :style="boardActionFrozenStyle(lkBoardAccess, 'logistics_filters')">
      <label>View</label>
      <select v-model="viewScope" @change="loadCards" class="lk-select lk-select-sm" :disabled="freezeLk('logistics_filters')">
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option>
        <option value="all">All dates</option>
      </select>
      <template v-if="viewScope === 'daily'">
        <label>Date</label>
        <input type="date" v-model="filterDate" @change="loadCards" class="lk-input-date" :disabled="freezeLk('logistics_filters')" />
      </template>
      <template v-else-if="viewScope === 'weekly'">
        <label>Week</label>
        <input type="week" v-model="filterWeek" @change="loadCards" class="lk-input-date" :disabled="freezeLk('logistics_filters')" />
      </template>
      <template v-else-if="viewScope === 'monthly'">
        <label>Month</label>
        <input type="month" v-model="filterMonth" @change="loadCards" class="lk-input-date" :disabled="freezeLk('logistics_filters')" />
      </template>
      <label>Order code</label>
      <input
        v-model="filterOrderCode"
        type="text"
        placeholder="Filter by order…"
        class="lk-input-text"
        :disabled="freezeLk('logistics_filters')"
        @keyup.enter="loadCards"
      />
      <label v-if="mode === 'despatch'">Clubbing Sheet</label>
      <input
        v-if="mode === 'despatch'"
        v-model="filterClubbingSheet"
        type="text"
        placeholder="Filter by club ID…"
        class="lk-input-text"
        :disabled="freezeLk('logistics_filters')"
        @keyup.enter="loadDespatchCards"
      />
      <button type="button" class="cc-clear-btn" :disabled="freezeLk('logistics_filters')" @click="loadCards">Apply</button>
    </div>

    <template v-if="mode === 'transfer'">
      <div class="lk-toolbar">
        <label>From company</label>
        <select v-model="fromCompany" @change="loadCards" class="lk-select">
          <option value="">Select company…</option>
          <option v-for="c in companies" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
        <label class="lk-filter-label">Show</label>
        <select v-model="historyFilter" class="lk-select lk-select-sm">
          <option value="all">All transfers</option>
          <option value="draft">Draft STE only</option>
          <option value="submitted">Submitted STE only</option>
        </select>
        <button type="button" class="lk-link-btn" :style="boardActionFrozenStyle(lkBoardAccess, 'logistics_transfer_approvals')" @click="guardedGoApprovals">Transfer Approvals →</button>
      </div>

      <div v-if="!fromCompany" class="lk-hint">Select a company to see transfer destination cards.</div>
      <div v-else-if="!destinationCards.length" class="lk-hint">No destination companies configured.</div>

      <div v-else class="lk-grid">
        <div
          v-for="(card, idx) in destinationCards"
          :key="card.company"
          class="lk-card-wrap"
          :style="{ animationDelay: `${idx * 60}ms` }"
        >
          <button type="button" class="lk-card" @click="openTransfer(card)">
            <span class="lk-card-icon">📦</span>
            <span class="lk-card-title">{{ card.label }}</span>
            <span class="lk-card-cta">Start transfer →</span>
          </button>

          <div v-if="filteredHistory(card).length" class="lk-history-panel">
            <div class="lk-history-head">
              Transfer history
              <span class="lk-history-hint">Drag draft STEs to set queue priority</span>
            </div>
            <div
              class="lk-history-list"
              @dragover.prevent
              @drop.prevent="onHistoryDrop(card)"
            >
              <div
                v-for="ste in filteredHistory(card)"
                :key="ste.name"
                class="lk-history-chip"
                :class="{
                  'is-draft': ste.docstatus === 0,
                  'is-done': ste.docstatus === 1,
                  'is-drag-over': dragOverSteName === ste.name,
                  'is-draggable': ste.docstatus === 0,
                }"
                :draggable="ste.docstatus === 0"
                @dragstart="onHistoryDragStart(card, ste, $event)"
                @dragover.prevent="onHistoryDragOver(ste)"
                @dragleave="onHistoryDragLeave(ste)"
                @dragend="onHistoryDragEnd"
                @click.stop="openSte(ste.name)"
              >
                <span v-if="ste.docstatus === 0" class="lk-drag-grip" title="Drag to reorder">⋮⋮</span>
                <span class="lk-history-badge">{{ formatStatus(ste.status) }}</span>
                <span class="lk-history-main">
                  <span class="lk-history-ste">{{ ste.name }}</span>
                  <span class="lk-history-meta">
                    <span v-if="ste.order_codes_label">Order Code {{ ste.order_codes_label }}</span>
                    <span v-if="ste.transfer_date"> · {{ formatDate(ste.transfer_date) }}</span>
                    <span v-if="ste.qty_total"> · {{ ste.qty_total }} Kg</span>
                  </span>
                </span>
                <span class="lk-history-go">Open →</span>
              </div>
            </div>
          </div>
          <p v-else class="lk-no-history">No transfers in this period.</p>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="lk-toolbar">
        <label>From company</label>
        <select v-model="fromCompany" @change="loadDespatchCards" class="lk-select">
          <option value="">All companies…</option>
          <option v-for="c in companies" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
        <button type="button" class="lk-link-btn" :style="boardActionFrozenStyle(lkBoardAccess, 'logistics_despatch_approvals')" @click="guardedGoDespatchApprovals">Despatch Approvals →</button>
      </div>

      <div v-if="!despatchCards.length" class="lk-hint">No company cards for despatch.</div>
      <div v-else class="lk-grid">
        <div v-for="(card, idx) in despatchCards" :key="card.company" class="lk-card-wrap" :style="{ animationDelay: `${idx * 60}ms` }">
          <button type="button" class="lk-card" @click="openDespatch(card)">
            <span class="lk-card-icon">📦</span>
            <span class="lk-card-title">{{ card.label }}</span>
            <span class="lk-card-cta">Start despatch →</span>
          </button>

          <div v-if="pendingListFor(card).length" class="lk-history-panel">
            <div class="lk-history-head lk-history-head-arrange">
              <span>Pending approval</span>
              <span class="lk-history-hint">Drag to set delivery priority — saves automatically</span>
              <div class="lk-arrange-btns">
                <button type="button" class="lk-arrange-btn lk-arrange-btn-lock" @click.stop="toggleDespatchArrangementLock">
                  {{ despatchArrangementLocked ? "🔒 Lock Arrangment" : "🔓 Unlock Arrangment" }}
                </button>
                <button type="button" class="lk-arrange-btn lk-arrange-btn-save" @click.stop="saveDespatchArrangement(card)">
                  💾 Save Arrangment
                </button>
                <button type="button" class="lk-arrange-btn lk-arrange-btn-restore" @click.stop="restoreDespatchArrangement(card)">
                  ↩ Restore Arrangment
                </button>
              </div>
            </div>
            <div class="lk-history-list" :data-lk-pending-list="card.company">
              <div
                v-for="da in pendingListFor(card)"
                :key="da.name"
                class="lk-history-chip is-pending"
                :class="{ 'is-draggable': !despatchArrangementLocked }"
                :data-approval-name="da.name"
                @click.stop="openDespatchApproval(da.name)"
              >
                <span v-if="!despatchArrangementLocked" class="lk-drag-grip" title="Drag to reorder">⋮⋮</span>
                <span class="lk-history-badge">Pending</span>
                <span class="lk-history-main">
                  <span class="lk-history-ste">{{ da.name }}</span>
                  <span class="lk-history-meta">
                    <span v-if="da.order_codes_label">Order {{ da.order_codes_label }}</span>
                    <span v-if="da.qty_total"> · {{ da.qty_total }} Kg</span>
                  </span>
                </span>
                <span class="lk-history-go">Review →</span>
              </div>
            </div>
          </div>

          <div v-if="approvedListFor(card).length" class="lk-history-panel">
            <div class="lk-history-head lk-history-head-arrange">
              <span>Approved despatch</span>
              <span class="lk-history-hint">Drag to set delivery queue — saves automatically</span>
              <div class="lk-arrange-btns">
                <button type="button" class="lk-arrange-btn lk-arrange-btn-lock" @click.stop="toggleApprovedArrangementLock">
                  {{ approvedArrangementLocked ? "🔒 Lock Arrangment" : "🔓 Unlock Arrangment" }}
                </button>
                <button type="button" class="lk-arrange-btn lk-arrange-btn-save" @click.stop="saveApprovedArrangement(card)">
                  💾 Save Arrangment
                </button>
                <button type="button" class="lk-arrange-btn lk-arrange-btn-restore" @click.stop="restoreApprovedArrangement(card)">
                  ↩ Restore Arrangment
                </button>
              </div>
            </div>
            <div class="lk-history-list lk-approved-sort-list" :data-lk-approved-list="card.company">
              <div
                v-for="da in approvedListFor(card)"
                :key="'a-' + da.name"
                class="lk-da-card"
                :data-approval-name="da.name"
                :class="[despatchCardClass(da), { 'is-club': !!da.clubbing_sheet }]"
                @click.stop="openDespatchApproval(da.name)"
              >
                <div class="lk-da-top">
                  <span v-if="!approvedArrangementLocked" class="lk-drag-grip lk-drag-grip-approved" title="Drag to reorder">⋮⋮</span>
                  <span class="lk-da-badge">{{ despatchCardBadge(da) }}</span>
                  <span class="lk-da-id">{{ da.clubbing_sheet || da.name }}</span>
                </div>
                <div class="lk-da-date-row" @click.stop>
                  <span class="lk-da-label">Despatch date</span>
                  <input
                    type="date"
                    class="lk-input-date lk-da-date-input"
                    :value="da.despatch_date || ''"
                    @change="moveDespatchToDate(da, $event.target.value)"
                  />
                  <button type="button" class="lk-dn-btn lk-dn-btn-scan lk-view-rolls-btn" @click="viewDespatchRolls(da)">
                    View Rolls
                  </button>
                </div>

                <template v-if="da.clubbing_sheet">
                  <div class="lk-da-grid">
                    <div class="lk-da-row">
                      <span class="lk-da-label">Approval</span>
                      <span class="lk-da-val">{{ da.name }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Orders</span>
                      <span class="lk-da-val">{{ da.order_codes_label || "—" }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Scan</span>
                      <span class="lk-da-val">{{ da.scanned_total || 0 }} / {{ da.scan_line_total || 0 }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Vehicle</span>
                      <span class="lk-da-val">{{ da.vehicle_no || "—" }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Driver</span>
                      <span class="lk-da-val">{{ da.driver || "—" }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Driver Ph</span>
                      <span class="lk-da-val">{{ da.driver_ph_no || "—" }}</span>
                    </div>
                  </div>
                  <div class="lk-club-orders" @click.stop>
                    <div
                      v-for="ord in da.club_orders || []"
                      :key="ord.party_code"
                      class="lk-club-order"
                      :class="{
                        'is-active': clubActiveOrder(da) === ord.party_code,
                        'is-done': (ord.scanned || 0) >= (ord.total || 0) && (ord.total || 0) > 0,
                      }"
                    >
                      <span class="lk-club-seq">{{ ord.loading_sequence || "—" }}</span>
                      <span class="lk-club-pc">{{ ord.party_code }}</span>
                      <span class="lk-club-prog">{{ ord.scanned || 0 }}/{{ ord.total || 0 }}</span>
                    </div>
                  </div>
                  <div v-if="da.dn_docstatus < 1 && !da.all_dns_submitted" class="lk-club-scan" @click.stop>
                    <input
                      :ref="(el) => setClubScanRef(da.name, el)"
                      :value="clubScanInput[da.name] || ''"
                      type="text"
                      class="lk-input-text lk-club-scan-input"
                      :placeholder="clubScanPlaceholder(da)"
                      :disabled="!!da.has_draft_dns || da.card_status === 'Draft DN' || da.scan_complete"
                      autocomplete="off"
                      inputmode="none"
                      @input="onClubScanTyped(da, $event)"
                      @keydown.enter.prevent="submitClubScan(da)"
                    />
                    <button
                      type="button"
                      class="lk-dn-btn lk-dn-btn-scan"
                      :disabled="!!da.has_draft_dns || da.card_status === 'Draft DN' || da.scan_complete"
                      @click="openClubBarcodeScanner(da)"
                    >
                      Scan barcode
                    </button>
                  </div>
                  <div v-if="da.clubbing_sheet && !da.scan_complete" class="lk-club-scan-hint" @click.stop>
                    Tap <b>Scan barcode</b> for camera (same as Delivery Note). USB gun: scan into the box — auto-adds.
                  </div>
                  <div class="lk-club-actions" @click.stop>
                    <button
                      v-if="!da.has_draft_dns && !da.delivery_notes?.length && da.dn_docstatus < 1"
                      type="button"
                      class="lk-dn-btn"
                      :disabled="!da.scan_complete"
                      @click="createClubDraftDns(da)"
                    >
                      Create Delivery Notes
                    </button>
                    <button
                      v-else
                      type="button"
                      class="lk-dn-btn lk-dn-btn-done"
                      @click="openDeliveryNote(da)"
                    >
                      {{ da.all_dns_submitted ? "Despatched — Open DN" : "Open Delivery Note" }}
                    </button>
                    <div v-if="da.delivery_notes?.length" class="lk-club-dn-list">
                      <span
                        v-for="dn in da.delivery_notes"
                        :key="dn"
                        class="lk-club-dn-item"
                      >
                        <a
                          href="#"
                          class="lk-club-dn-link"
                          @click.prevent="openDnForm(dn)"
                        >{{ dn }}</a>
                        <button
                          v-if="da.dn_docstatus < 1"
                          type="button"
                          class="lk-club-dn-del"
                          title="Delete draft DN"
                          @click.stop="deleteClubDraftDn(da, dn)"
                        >×</button>
                      </span>
                    </div>
                  </div>
                </template>

                <template v-else>
                  <div class="lk-da-grid">
                    <div class="lk-da-row">
                      <span class="lk-da-label">Order code</span>
                      <span class="lk-da-val">{{ da.order_codes_label || "—" }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Customer</span>
                      <span class="lk-da-val">{{ da.customers_label || "—" }}</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Items / Rolls</span>
                      <span class="lk-da-val">{{ da.item_count || 0 }} · {{ da.roll_count || 0 }} rolls</span>
                    </div>
                    <div class="lk-da-row">
                      <span class="lk-da-label">Qty</span>
                      <span class="lk-da-val">{{ da.qty_total || 0 }} Kg</span>
                    </div>
                  </div>
                  <button
                    v-if="da.dn_docstatus < 1"
                    type="button"
                    class="lk-dn-btn"
                    @click.stop="openDeliveryNote(da)"
                  >
                    {{ despatchDnButtonLabel(da) }}
                  </button>
                  <button
                    v-else
                    type="button"
                    class="lk-dn-btn lk-dn-btn-done"
                    @click.stop="openDeliveryNote(da)"
                  >
                    Despatched — Open DN
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <TransferDialog
      v-model="showDialog"
      board-kind="production"
      :filter-context="dialogFilters"
      :prefill="dialogPrefill"
      @submitted="loadCards"
    />
    <DespatchDialog
      v-model="showDespatchDialog"
      board-kind="production"
      :filter-context="dialogFilters"
      :prefill="despatchPrefill"
      @submitted="onDespatchSubmitted"
    />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import Sortable from "sortablejs";
import TransferDialog from "./TransferDialog.vue";
import DespatchDialog from "./DespatchDialog.vue";
import { boardActionFrozenStyle, isBoardActionFrozen } from "./board_access_ui.js";

const API = "production_entry.production_planning.transfer_logistics";
const DESPATCH_API = "production_entry.production_planning.despatch_logistics";
const lkBoardAccess = ref({
  unlimited: true,
  allowed_units: [],
  loaded: false,
  permitted: true,
  frozen_actions: {},
});

function freezeLk(action) {
  return isBoardActionFrozen(lkBoardAccess.value, action);
}

function guardedSetMode(m) {
  const key = m === "despatch" ? "logistics_despatch" : "logistics_transfer";
  if (freezeLk(key)) {
    frappe.msgprint(__("This Logistics mode is frozen for your access."));
    return;
  }
  setMode(m);
}

function guardedGoApprovals() {
  if (freezeLk("logistics_transfer_approvals")) {
    frappe.msgprint(__("Transfer Approvals is frozen for your access."));
    return;
  }
  goApprovals();
}

function guardedGoDespatchApprovals() {
  if (freezeLk("logistics_despatch_approvals")) {
    frappe.msgprint(__("Despatch Approvals is frozen for your access."));
    return;
  }
  goDespatchApprovals();
}

async function loadLkBoardAccess() {
  await new Promise((resolve) => {
    frappe.call({
      method: "production_entry.production_planning.board_access.get_production_board_user_context",
      args: { board_slug: "logistics-kanban" },
      callback: (r) => {
        lkBoardAccess.value = { ...((r && r.message) || {}), loaded: true };
        resolve();
      },
      error: () => {
        lkBoardAccess.value = { unlimited: true, loaded: true, frozen_actions: {} };
        resolve();
      },
    });
  });
}

const mode = ref("transfer");
const mounted = ref(false);
const gateOpen = ref(false);
const historyFilter = ref("all");
const viewScope = ref("daily");
const filterDate = ref(frappe.datetime.get_today());
const filterWeek = ref("");
const filterMonth = ref("");
const filterOrderCode = ref("");
const filterClubbingSheet = ref("");
const clubScanInput = ref({});
const clubScanRefs = {};
const clubScanTypedTimers = {};
const activeClubScanDa = ref("");
const companies = ref([]);
const fromCompany = ref("Jayashree Spun Bond - 1ZT");
const destinationCards = ref([]);
const showDialog = ref(false);
const showDespatchDialog = ref(false);
const dialogPrefill = ref({});
const despatchPrefill = ref({});
const despatchCards = ref([]);
const dialogFilters = ref({ view_scope: "daily", date: frappe.datetime.get_today() });
const dragLane = ref(null);
const dragSte = ref(null);
const dragOverSteName = ref("");
const pendingOrderByCompany = ref({});
const approvedOrderByCompany = ref({});
const despatchArrangementLocked = ref(true);
const approvedArrangementLocked = ref(true);
const despatchSortableBusy = ref(false);
const pendingSortables = [];
const approvedSortables = [];
let refreshTimer = null;

function initWeekMonth() {
  const d = new Date();
  if (!filterWeek.value) {
    const onejan = new Date(d.getFullYear(), 0, 1);
    const week = Math.ceil(((d - onejan) / 86400000 + onejan.getDay() + 1) / 7);
    filterWeek.value = `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
  }
  if (!filterMonth.value) {
    filterMonth.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
}

function setMode(m) {
  mode.value = m;
  if (m === "despatch") loadDespatchCards();
  else loadCards();
}

function formatDate(d) {
  if (!d) return "";
  try {
    return frappe.datetime.str_to_user(d);
  } catch {
    return String(d).slice(0, 10);
  }
}

function filteredHistory(card) {
  const list = card.transfer_history || card.draft_stock_entries || [];
  if (historyFilter.value === "draft") {
    return list.filter((x) => x.docstatus === 0 || x.status === "Draft");
  }
  if (historyFilter.value === "submitted") {
    return list.filter((x) => x.docstatus === 1 || x.status === "Submitted");
  }
  return list;
}

function draftSteOrder(card) {
  return filteredHistory(card)
    .filter((s) => s.docstatus === 0)
    .map((s) => s.name);
}

function formatStatus(status) {
  if (!status) return "";
  if (status.startsWith("Transferred")) return "Transferred";
  return status;
}

function onHistoryDragStart(card, ste, ev) {
  if (ste.docstatus !== 0) {
    ev.preventDefault();
    return;
  }
  dragLane.value = card;
  dragSte.value = ste;
  try {
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", ste.name);
  } catch (e) {}
}

function onHistoryDragOver(ste) {
  if (ste.docstatus !== 0) return;
  dragOverSteName.value = ste.name;
}

function onHistoryDragLeave(ste) {
  if (dragOverSteName.value === ste.name) dragOverSteName.value = "";
}

function onHistoryDragEnd() {
  dragSte.value = null;
  dragLane.value = null;
  dragOverSteName.value = "";
}

async function onHistoryDrop(card) {
  const drag = dragSte.value;
  const lane = dragLane.value;
  if (!drag || !lane || lane.company !== card.company) {
    onHistoryDragEnd();
    return;
  }
  const drafts = draftSteOrder(card);
  const from = drag.name;
  const to = dragOverSteName.value;
  if (to && from !== to) {
    const fi = drafts.indexOf(from);
    const ti = drafts.indexOf(to);
    if (fi >= 0 && ti >= 0) {
      drafts.splice(fi, 1);
      drafts.splice(ti, 0, from);
    }
  }
  try {
    await frappe.call({
      method: `${API}.reorder_transfer_lane_queue`,
      args: {
        from_company: fromCompany.value,
        to_company: card.company,
        ste_names: JSON.stringify(drafts),
      },
    });
    frappe.show_alert({ message: __("Queue order saved"), indicator: "green" });
  } catch (e) {
    frappe.show_alert({ message: __("Could not save queue order"), indicator: "red" });
  }
  onHistoryDragEnd();
  await loadCards();
}

function cardLoadArgs() {
  const args = { from_company: fromCompany.value };
  if (viewScope.value !== "all") {
    args.view_scope = viewScope.value;
    if (viewScope.value === "daily") args.date = filterDate.value;
    if (viewScope.value === "weekly") args.week = filterWeek.value;
    if (viewScope.value === "monthly") args.month = filterMonth.value;
  } else {
    args.view_scope = "all";
  }
  if ((filterOrderCode.value || "").trim()) {
    args.order_code = filterOrderCode.value.trim();
  }
  return args;
}

async function loadCompanies() {
  const r = await frappe.call({ method: `${API}.get_logistics_companies` });
  companies.value = r.message || [];
}

async function loadCards() {
  if (!fromCompany.value) {
    destinationCards.value = [];
    return;
  }
  dialogFilters.value = {
    view_scope: viewScope.value === "all" ? "daily" : viewScope.value,
    date: filterDate.value,
    week: filterWeek.value,
    month: filterMonth.value,
    party_code: filterOrderCode.value,
  };
  const r = await frappe.call({
    method: `${API}.get_transfer_destination_cards`,
    args: cardLoadArgs(),
  });
  destinationCards.value = r.message || [];
}

function openTransfer(card) {
  dialogPrefill.value = {
    from_company: fromCompany.value,
    to_company: card.company,
    party_code: filterOrderCode.value || "",
    customer: "",
  };
  showDialog.value = true;
}

function openSte(name) {
  frappe.set_route("Form", "Stock Entry", name);
}

function goApprovals() {
  frappe.set_route("transfer-approval-dashboard");
}

function goDespatchApprovals() {
  frappe.set_route("despatch-approval-dashboard");
}

function syncPendingOrderFromCards(cards) {
  if (despatchSortableBusy.value) return;
  const map = { ...(pendingOrderByCompany.value || {}) };
  (cards || []).forEach((card) => {
    map[card.company] = [...(card.pending_approvals || [])];
  });
  pendingOrderByCompany.value = map;
}

function syncApprovedOrderFromCards(cards) {
  if (despatchSortableBusy.value) return;
  const map = { ...(approvedOrderByCompany.value || {}) };
  (cards || []).forEach((card) => {
    map[card.company] = [...(card.approved_approvals || [])];
  });
  approvedOrderByCompany.value = map;
}

function pendingListFor(card) {
  const list = pendingOrderByCompany.value[card.company];
  if (Array.isArray(list) && list.length) return list;
  return card.pending_approvals || [];
}

function approvedListFor(card) {
  const list = approvedOrderByCompany.value[card.company];
  let rows = Array.isArray(list) && list.length ? list : card.approved_approvals || [];
  const club = (filterClubbingSheet.value || "").trim().toLowerCase();
  if (club) {
    rows = rows.filter((a) => (a.clubbing_sheet || "").toLowerCase().includes(club));
  }
  return rows;
}

function reorderCardsFromDom(container, card, scope) {
  const names = [...container.querySelectorAll("[data-approval-name]")].map((el) => el.dataset.approvalName);
  const source = scope === "pending" ? pendingListFor(card) : approvedListFor(card);
  const byName = Object.fromEntries(source.map((a) => [a.name, a]));
  const ordered = names.map((n) => byName[n]).filter(Boolean);
  if (scope === "pending") {
    pendingOrderByCompany.value = { ...pendingOrderByCompany.value, [card.company]: ordered };
  } else {
    approvedOrderByCompany.value = { ...approvedOrderByCompany.value, [card.company]: ordered };
  }
  return names;
}

function destroyDespatchSortables() {
  [...pendingSortables, ...approvedSortables].forEach((s) => {
    try {
      s.destroy();
    } catch (e) {}
  });
  pendingSortables.length = 0;
  approvedSortables.length = 0;
}

async function persistPendingOrder(card, names) {
  if (!names?.length) return;
  await frappe.call({
    method: `${DESPATCH_API}.save_despatch_pending_arrangement`,
    args: { from_company: card.company, approval_names: JSON.stringify(names) },
  });
}

async function persistApprovedOrder(card, names) {
  if (!names?.length) return;
  await frappe.call({
    method: `${DESPATCH_API}.save_despatch_approved_arrangement`,
    args: { from_company: card.company, approval_names: JSON.stringify(names) },
  });
}

async function initDespatchSortables() {
  await nextTick();
  destroyDespatchSortables();
  if (mode.value !== "despatch") return;

  document.querySelectorAll("[data-lk-pending-list]").forEach((el) => {
    const company = el.dataset.lkPendingList;
    const card = despatchCards.value.find((c) => c.company === company);
    if (!card) return;
    const s = new Sortable(el, {
      animation: 180,
      handle: ".lk-drag-grip",
      draggable: ".lk-history-chip",
      disabled: despatchArrangementLocked.value,
      ghostClass: "lk-sort-ghost",
      onStart() {
        despatchSortableBusy.value = true;
      },
      onEnd: async () => {
        try {
          const names = reorderCardsFromDom(el, card, "pending");
          await persistPendingOrder(card, names);
          frappe.show_alert({ message: __("Pending priority saved"), indicator: "green" });
        } catch (e) {
          frappe.show_alert({ message: __("Could not save priority"), indicator: "red" });
        } finally {
          despatchSortableBusy.value = false;
        }
      },
    });
    pendingSortables.push(s);
  });

  document.querySelectorAll("[data-lk-approved-list]").forEach((el) => {
    const company = el.dataset.lkApprovedList;
    const card = despatchCards.value.find((c) => c.company === company);
    if (!card) return;
    const s = new Sortable(el, {
      animation: 180,
      handle: ".lk-drag-grip-approved",
      draggable: ".lk-da-card",
      disabled: approvedArrangementLocked.value,
      ghostClass: "lk-sort-ghost",
      onStart() {
        despatchSortableBusy.value = true;
      },
      onEnd: async () => {
        try {
          const names = reorderCardsFromDom(el, card, "approved");
          await persistApprovedOrder(card, names);
          frappe.show_alert({ message: __("Approved queue saved"), indicator: "green" });
        } catch (e) {
          frappe.show_alert({ message: __("Could not save queue"), indicator: "red" });
        } finally {
          despatchSortableBusy.value = false;
        }
      },
    });
    approvedSortables.push(s);
  });
}

function toggleDespatchArrangementLock() {
  despatchArrangementLocked.value = !despatchArrangementLocked.value;
  pendingSortables.forEach((s) => {
    s.option("disabled", despatchArrangementLocked.value);
  });
}

function toggleApprovedArrangementLock() {
  approvedArrangementLocked.value = !approvedArrangementLocked.value;
  approvedSortables.forEach((s) => {
    s.option("disabled", approvedArrangementLocked.value);
  });
}

async function saveDespatchArrangement(card) {
  const names = pendingListFor(card).map((a) => a.name);
  if (!names.length) return;
  try {
    await persistPendingOrder(card, names);
    frappe.show_alert({ message: __("Despatch priority saved"), indicator: "green" });
  } catch (e) {
    frappe.show_alert({ message: __("Could not save arrangement"), indicator: "red" });
  }
}

async function restoreDespatchArrangement(card) {
  try {
    await frappe.call({
      method: `${DESPATCH_API}.restore_despatch_pending_arrangement`,
      args: { from_company: card.company },
    });
    frappe.show_alert({ message: __("Arrangement restored"), indicator: "green" });
    await loadDespatchCards();
  } catch (e) {
    frappe.msgprint(e?.message || __("No previous arrangement to restore."));
  }
}

async function saveApprovedArrangement(card) {
  const names = approvedListFor(card).map((a) => a.name);
  if (!names.length) return;
  try {
    await persistApprovedOrder(card, names);
    frappe.show_alert({ message: __("Approved queue saved"), indicator: "green" });
  } catch (e) {
    frappe.show_alert({ message: __("Could not save arrangement"), indicator: "red" });
  }
}

async function restoreApprovedArrangement(card) {
  try {
    await frappe.call({
      method: `${DESPATCH_API}.restore_despatch_approved_arrangement`,
      args: { from_company: card.company },
    });
    frappe.show_alert({ message: __("Arrangement restored"), indicator: "green" });
    await loadDespatchCards();
  } catch (e) {
    frappe.msgprint(e?.message || __("No previous arrangement to restore."));
  }
}

async function loadDespatchCards() {
  dialogFilters.value = {
    view_scope: viewScope.value === "all" ? "daily" : viewScope.value,
    date: filterDate.value,
    week: filterWeek.value,
    month: filterMonth.value,
    party_code: filterOrderCode.value,
  };
  const args = { from_company: fromCompany.value || "" };
  if (viewScope.value !== "all") {
    args.view_scope = viewScope.value;
    if (viewScope.value === "daily") args.date = filterDate.value;
    if (viewScope.value === "weekly") args.week = filterWeek.value;
    if (viewScope.value === "monthly") args.month = filterMonth.value;
  } else {
    args.view_scope = "all";
  }
  if ((filterOrderCode.value || "").trim()) args.order_code = filterOrderCode.value.trim();
  const r = await frappe.call({ method: `${DESPATCH_API}.get_despatch_company_cards`, args });
  despatchCards.value = r.message || [];
  syncPendingOrderFromCards(despatchCards.value);
  syncApprovedOrderFromCards(despatchCards.value);
  await initDespatchSortables();
}

function openDespatch(card) {
  despatchPrefill.value = {
    from_company: card.company || fromCompany.value,
  };
  showDespatchDialog.value = true;
}

function openDespatchApproval(name) {
  frappe.route_options = { approval: name };
  frappe.set_route("despatch-approval-dashboard");
}

function viewDespatchRolls(da) {
  if (!da?.name) return;
  frappe.call({
    method: `${DESPATCH_API}.get_despatch_approval_roll_list`,
    args: { approval_name: da.name },
    freeze: true,
    freeze_message: __("Loading rolls…"),
    callback(r) {
      const msg = r.message || {};
      const rolls = msg.rolls || [];
      if (!rolls.length) {
        frappe.msgprint(__("No batches selected on this Despatch Approval."));
        return;
      }
      if (typeof jsb_show_despatch_rolls_dialog === "function") {
		jsb_show_despatch_rolls_dialog({
          rolls,
          order_code: da.order_codes_label || "",
          sales_order: da.order_codes_label || da.clubbing_sheet || da.name,
          delivery_note: (da.delivery_notes && da.delivery_notes[0]) || da.delivery_note || "",
        });
        return;
      }
      frappe.msgprint(__("Roll print dialog not loaded — hard refresh (Ctrl+Shift+R)."));
    },
  });
}

function despatchCardBadge(da) {
  if (da.all_dns_submitted || da.dn_docstatus >= 1 || da.card_status === "Despatched") return "Despatched";
  if (da.has_draft_dns || da.delivery_note || da.card_status === "Draft DN") return "Draft DN";
  if (da.clubbing_sheet && !da.scan_complete) return "Scan load";
  return "Approved";
}

function despatchCardClass(da) {
  if (da.all_dns_submitted || da.dn_docstatus >= 1) return "is-despatched";
  if (da.has_draft_dns || da.delivery_note) return "is-draft-dn";
  return "is-done";
}

function despatchDnButtonLabel(da) {
  if (da.delivery_note || (da.delivery_notes && da.delivery_notes.length)) return __("Open Draft DN");
  return __("Create Delivery Note");
}

function clubActiveOrder(da) {
  const orders = da.club_orders || [];
  for (const o of orders) {
    if ((o.scanned || 0) < (o.total || 0)) return o.party_code;
  }
  return "";
}

function clubScanPlaceholder(da) {
  const pc = clubActiveOrder(da);
  return pc ? __("Scan batch for {0}…", [pc]) : __("All rolls scanned");
}

function setClubScanRef(name, el) {
  if (el) clubScanRefs[name] = el;
}

function focusClubScanInput(da) {
  nextTick(() => {
    const el = clubScanRefs[da?.name];
    if (el && typeof el.focus === "function") el.focus();
  });
}

function formatClubScanError(e) {
  if (!e) return __("Unknown error");
  if (typeof e === "string") return e;
  if (e.message && typeof e.message === "string" && e.message !== "[object Object]") {
    return e.message;
  }
  if (e._server_messages) {
    try {
      const msgs = JSON.parse(e._server_messages);
      const parts = (msgs || []).map((m) => {
        try {
          return JSON.parse(m).message;
        } catch {
          return typeof m === "string" ? m : "";
        }
      }).filter(Boolean);
      if (parts.length) return parts.join("; ");
    } catch { /* ignore */ }
  }
  if (e.exc_type) return `${e.exc_type}: ${e.message || ""}`;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

/** USB gun finished barcode (avoid auto-submit mid-scan like JS-/105268/). */
function looksLikeCompleteBatchBarcode(v) {
  const s = String(v || "").trim();
  if (!s || s.length < 10) return false;
  // Ends with /rollNo — typical JS-…/…/N or JS-/105268//2
  if (/\/\d+\s*$/.test(s)) return true;
  // Long enough with slash and no trailing incomplete slash-only
  if (s.includes("/") && !s.endsWith("/") && s.replace(/[^A-Za-z0-9]/g, "").length >= 8) {
    return true;
  }
  return s.length >= 14 && !s.includes("/");
}

async function submitClubScan(da, forcedBarcode) {
  const bc = (forcedBarcode || clubScanInput.value[da.name] || "").trim();
  if (!bc) {
    openClubBarcodeScanner(da);
    return;
  }
  try {
    const r = await frappe.call({
      method: `${DESPATCH_API}.record_despatch_club_scan`,
      args: { name: da.name, barcode: bc },
    });
    const msg = r.message || {};
    clubScanInput.value = { ...clubScanInput.value, [da.name]: "" };
    frappe.show_alert({
      message: msg.message || __("Scanned"),
      indicator: msg.duplicate ? "orange" : "green",
    });
    await loadDespatchCards();
    focusClubScanInput(da);
  } catch (e) {
    frappe.msgprint(formatClubScanError(e));
    focusClubScanInput(da);
  }
}

function onClubScanTyped(da, ev) {
  const v = (ev?.target?.value || "").trim();
  clubScanInput.value = { ...clubScanInput.value, [da.name]: ev?.target?.value || "" };
  // USB guns type fast then Enter; auto-submit only when barcode looks complete
  if (!v) return;
  clearTimeout(clubScanTypedTimers[da.name]);
  clubScanTypedTimers[da.name] = setTimeout(() => {
    const cur = (clubScanInput.value[da.name] || "").trim();
    if (cur && cur === v && looksLikeCompleteBatchBarcode(cur)) {
      submitClubScan(da, cur);
    }
  }, 350);
}

function openClubBarcodeScanner(da) {
  // Same engine as Delivery Note "Scan QRCode" (html5-qrcode via frappe.ui.Scanner)
  if (typeof frappe.ui.Scanner !== "function") {
    frappe.msgprint(__("Scanner not loaded. Hard-refresh the page, or use the USB barcode gun in the scan box."));
    focusClubScanInput(da);
    return;
  }
  try {
    // eslint-disable-next-line no-new
    new frappe.ui.Scanner({
      dialog: true,
      multiple: true,
      on_scan(data) {
        const code = String(data?.decodedText || data?.result?.text || data || "").trim();
        if (code) {
          submitClubScan(da, code);
        }
      },
    });
  } catch (e) {
    frappe.msgprint(formatClubScanError(e) || __("Could not open camera scanner."));
    focusClubScanInput(da);
  }
}

async function createClubDraftDns(da) {
  try {
    const r = await frappe.call({
      method: `${DESPATCH_API}.create_draft_delivery_notes_from_despatch`,
      args: { name: da.name },
      freeze: true,
      freeze_message: __("Creating Delivery Notes…"),
    });
    const notes = r.message?.delivery_notes || [];
    frappe.show_alert({
      message: __("Created {0} draft DN(s)", [String(notes.length)]),
      indicator: "green",
    });
    await loadDespatchCards();
  } catch (e) {
    frappe.msgprint(formatClubScanError(e));
  }
}

function openDnForm(dn) {
  if (dn) frappe.set_route("Form", "Delivery Note", dn);
}

async function deleteClubDraftDn(da, dn) {
  if (!dn || !da?.name) return;
  frappe.confirm(
    __("Delete draft DN {0} only for this Despatch Approval? Other DNs for this club are not affected.", [dn]),
    async () => {
      try {
        await frappe.call({
          method: `${DESPATCH_API}.delete_draft_delivery_note`,
          args: { delivery_note: dn, despatch_approval: da.name },
          freeze: true,
          freeze_message: __("Deleting Delivery Note…"),
        });
        frappe.show_alert({ message: __("Deleted {0}", [dn]), indicator: "green" });
        await loadDespatchCards();
      } catch (e) {
        frappe.msgprint(formatClubScanError(e));
      }
    }
  );
}

async function moveDespatchToDate(da, laneDate) {
  if (!da?.name || !laneDate || laneDate === da.despatch_date) return;
  try {
    await frappe.call({
      method: `${DESPATCH_API}.move_despatch_approval_to_date`,
      args: { name: da.name, lane_date: laneDate },
      freeze: true,
      freeze_message: __("Moving despatch date…"),
    });
    frappe.show_alert({ message: __("Moved to {0}", [laneDate]), indicator: "green" });
    await loadDespatchCards();
  } catch (e) {
    frappe.msgprint(formatClubScanError(e));
  }
}

async function openDeliveryNote(da) {
  if (!da?.name) return;
  const first = (da.delivery_notes && da.delivery_notes[0]) || da.delivery_note;
  if (first) {
    frappe.set_route("Form", "Delivery Note", first);
    return;
  }
  try {
    const r = await frappe.call({
      method: `${DESPATCH_API}.prepare_delivery_note_from_despatch_approval`,
      args: { name: da.name },
    });
    const msg = r.message || {};
    if (msg.mode === "existing" && msg.delivery_note) {
      frappe.set_route("Form", "Delivery Note", msg.delivery_note);
      return;
    }
    if (msg.mode !== "new" || !msg.doc) {
      frappe.msgprint(__("Could not open Delivery Note."));
      return;
    }
    frappe.set_route("List", "Delivery Note");
  } catch (e) {
    frappe.msgprint(formatClubScanError(e));
  }
}

function onDespatchSubmitted() {
  loadDespatchCards();
}

function reloadBoard() {
  if (mode.value === "despatch") loadDespatchCards();
  else loadCards();
}

watch(fromCompany, reloadBoard);
watch([viewScope, filterDate, filterWeek, filterMonth, filterOrderCode, filterClubbingSheet], reloadBoard);

onMounted(async () => {
  await loadLkBoardAccess();
  initWeekMonth();
  requestAnimationFrame(() => {
    mounted.value = true;
    setTimeout(() => {
      gateOpen.value = true;
    }, 120);
  });
  loadCompanies();
  loadCards();
  refreshTimer = window.setInterval(() => {
    if (showDialog.value || showDespatchDialog.value) return;
    if (despatchSortableBusy.value) return;
    if (mode.value === "despatch") loadDespatchCards();
    else loadCards();
  }, 60000);
});

onUnmounted(() => {
  destroyDespatchSortables();
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
});

watch([despatchArrangementLocked, approvedArrangementLocked, mode], () => {
  if (mode.value === "despatch") initDespatchSortables();
});
</script>

<style scoped>
.lk-container {
  position: relative;
  padding: 20px 24px 32px;
  font-family: system-ui, sans-serif;
  background: linear-gradient(160deg, #f0f9ff 0%, #f8fafc 45%, #f1f5f9 100%);
  min-height: calc(100vh - 80px);
  opacity: 0;
  transform: translateY(10px);
  transition: opacity 0.45s ease, transform 0.45s ease;
  overflow: hidden;
}
.lk-container.lk-mounted {
  opacity: 1;
  transform: none;
}
.lk-gate {
  position: fixed;
  top: 0;
  bottom: 0;
  width: 48vw;
  background: linear-gradient(90deg, #042f49 0%, #0c4a6e 40%, #0369a1 100%);
  z-index: 9998;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    transform 0.85s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.6s ease;
  box-shadow: 0 0 40px rgba(3, 105, 161, 0.45);
}
.lk-gate-label {
  color: rgba(255, 255, 255, 0.35);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.2em;
}
.lk-gate-left {
  left: 0;
  transform-origin: left center;
}
.lk-gate-right {
  right: 0;
  transform-origin: right center;
  background: linear-gradient(270deg, #042f49 0%, #0c4a6e 40%, #0369a1 100%);
}
.lk-container:not(.lk-gate-open) .lk-gate-left {
  transform: translateX(0);
  opacity: 1;
}
.lk-container:not(.lk-gate-open) .lk-gate-right {
  transform: translateX(0);
  opacity: 1;
}
.lk-container.lk-gate-open .lk-gate-left {
  transform: translateX(-102%);
  opacity: 0.6;
}
.lk-container.lk-gate-open .lk-gate-right {
  transform: translateX(102%);
  opacity: 0.6;
}
.lk-hero {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px 24px;
  margin-bottom: 16px;
  padding: 20px 24px;
  background: linear-gradient(135deg, #0c4a6e 0%, #0369a1 55%, #0ea5e9 100%);
  border-radius: 16px;
  color: #ffffff;
  box-shadow: 0 8px 24px rgba(3, 105, 161, 0.25);
  position: relative;
  z-index: 1;
  overflow: hidden;
}
.lk-title {
  margin: 0;
  font-size: 28px;
  font-weight: 800;
  color: #ffffff;
}
.lk-subtitle {
  margin: 6px 0 0;
  color: rgba(255, 255, 255, 0.92);
  font-size: 13px;
  font-weight: 600;
}
.lk-hero-text {
  flex: 1;
  min-width: 180px;
}
.lk-scene-transfer {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  width: 260px;
  min-height: 48px;
}
.lk-site {
  font-size: 24px;
  line-height: 1;
  opacity: 0.95;
  flex-shrink: 0;
}
.lk-road-transfer {
  position: relative;
  flex: 1;
  height: 36px;
  border-bottom: 3px dashed rgba(255, 255, 255, 0.55);
}
.lk-truck-transfer {
  position: absolute;
  bottom: 8px;
  left: 0;
  font-size: 24px;
  line-height: 1;
  transform: scaleX(-1);
  animation: lk-transfer-end-to-end 5s linear infinite;
}
@keyframes lk-transfer-end-to-end {
  0% {
    left: 0;
    transform: scaleX(-1);
  }
  48% {
    left: calc(100% - 28px);
    transform: scaleX(-1);
  }
  50% {
    left: calc(100% - 28px);
    transform: scaleX(1);
  }
  98% {
    left: 0;
    transform: scaleX(1);
  }
  100% {
    left: 0;
    transform: scaleX(-1);
  }
}
.lk-scene-despatch {
  position: relative;
  width: 220px;
  height: 44px;
  flex-shrink: 0;
}
.lk-road-despatch {
  position: absolute;
  bottom: 6px;
  left: 0;
  right: 40px;
  height: 4px;
  background: rgba(255, 255, 255, 0.45);
  border-radius: 2px;
}
.lk-truck-despatch {
  position: absolute;
  bottom: 10px;
  left: 0;
  font-size: 24px;
  /* Emoji truck faces left by default — flip so it runs toward customer (right). */
  transform: scaleX(-1);
  animation: lk-despatch-to-customer 4s ease-in-out infinite;
}
.lk-customer {
  position: absolute;
  bottom: 8px;
  right: 0;
  font-size: 26px;
}
@keyframes lk-despatch-to-customer {
  0% {
    left: 0;
    transform: scaleX(1);
  }
  92% {
    left: calc(100% - 36px);
    transform: scaleX(1);
  }
  100% {
    left: calc(100% - 36px);
    transform: scaleX(1);
  }
}
.lk-toggle {
  display: flex;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.35);
  z-index: 1;
}
.lk-toggle button {
  padding: 10px 20px;
  border: none;
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  cursor: pointer;
  font-weight: 600;
  font-size: 13px;
}
.lk-toggle button.active {
  background: #fff;
  color: #0369a1;
}
.lk-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  position: relative;
  z-index: 1;
}
.lk-filters label {
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
  text-transform: uppercase;
}
.lk-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 20px;
  position: relative;
  z-index: 1;
}
.lk-toolbar label {
  font-size: 12px;
  font-weight: 700;
  color: #475569;
}
.lk-select {
  min-width: 260px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
}
.lk-select-sm {
  min-width: 150px;
}
.lk-input-date,
.lk-input-text {
  padding: 7px 10px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  font-size: 13px;
}
.lk-input-text {
  min-width: 140px;
}
.lk-link-btn {
  margin-left: auto;
  background: #fff;
  border: 1px solid #0ea5e9;
  color: #0369a1;
  padding: 8px 14px;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
}
.lk-hint,
.lk-no-history {
  color: #64748b;
  padding: 16px;
  text-align: center;
  font-size: 13px;
}
.lk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  position: relative;
  z-index: 1;
}
.lk-mode-despatch .lk-grid {
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
}
.lk-card-wrap {
  opacity: 0;
  animation: lk-card-in 0.45s ease forwards;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
@keyframes lk-card-in {
  to {
    opacity: 1;
    transform: none;
  }
}
.lk-card {
  text-align: left;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 20px 18px;
  cursor: pointer;
  background: #fff;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.lk-card:hover {
  border-color: #0ea5e9;
  box-shadow: 0 8px 20px rgba(14, 165, 233, 0.12);
}
.lk-card-icon {
  font-size: 28px;
}
.lk-card-title {
  font-size: 17px;
  font-weight: 800;
  color: #0f172a;
  line-height: 1.25;
}
.lk-card-cta {
  font-size: 13px;
  font-weight: 700;
  color: #0284c7;
  margin-top: 4px;
}
.lk-history-panel {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px;
}
.lk-history-head {
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  color: #475569;
  margin-bottom: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.lk-history-hint {
  font-size: 10px;
  font-weight: 600;
  text-transform: none;
  color: #94a3b8;
}
.lk-history-head-arrange {
  gap: 6px;
}
.lk-arrange-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}
.lk-arrange-btn {
  font-size: 11px;
  font-weight: 700;
  padding: 7px 14px;
  border-radius: 999px;
  border: none;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.12);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}
.lk-arrange-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.18);
}
.lk-arrange-btn-lock {
  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
  color: #334155;
  border: 1px solid #cbd5e1;
}
.lk-arrange-btn-save {
  background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%);
  color: #fff;
}
.lk-arrange-btn-restore {
  background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
  color: #fff;
}
.lk-history-chip.is-pending.is-draggable,
.lk-da-card .lk-drag-grip-approved {
  cursor: grab;
}
.lk-approved-sort-list {
  gap: 10px;
}
.lk-sort-ghost {
  opacity: 0.45;
  background: #e0f2fe !important;
  border: 2px dashed #0ea5e9 !important;
}
.lk-drag-grip-approved {
  margin-right: 4px;
  flex-shrink: 0;
}
.lk-history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.lk-history-chip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  border-radius: 10px;
  padding: 12px 12px;
  cursor: pointer;
  border: 1px solid #e2e8f0;
  background: #fff;
  text-align: left;
  transition: box-shadow 0.15s ease, border-color 0.15s ease;
}
.lk-history-chip.is-draggable {
  cursor: grab;
}
.lk-history-chip.is-drag-over {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 2px rgba(14, 165, 233, 0.25);
}
.lk-history-chip.is-draft {
  border-color: #fcd34d;
  background: #fffbeb;
}
.lk-history-chip.is-done {
  border-color: #86efac;
  background: #f0fdf4;
}
.lk-drag-grip {
  font-size: 14px;
  color: #94a3b8;
  line-height: 1;
  padding-top: 2px;
  user-select: none;
}
.lk-history-badge {
  font-size: 10px;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 6px;
  color: #fff;
  flex-shrink: 0;
}
.lk-history-chip.is-draft .lk-history-badge {
  background: #f59e0b;
}
.lk-history-chip.is-done .lk-history-badge {
  background: #16a34a;
}
.lk-history-main {
  flex: 1;
  min-width: 0;
}
.lk-history-ste {
  font-size: 14px;
  font-weight: 800;
  font-family: ui-monospace, monospace;
  color: #0f172a;
  display: block;
}
.lk-history-meta {
  font-size: 13px;
  color: #475569;
  display: block;
  margin-top: 4px;
  line-height: 1.35;
}
.lk-history-chip.is-pending .lk-history-badge {
  background: #ea580c;
}
.lk-history-chip.is-despatched .lk-history-badge {
  background: #0369a1;
}
.lk-history-chip.is-despatched {
  border-color: #bae6fd;
  background: #f0f9ff;
}
.lk-da-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
  cursor: pointer;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
}
.lk-da-card.is-draft-dn {
  border-color: #fde68a;
  background: #fffbeb;
}
.lk-da-card.is-despatched {
  border-color: #bae6fd;
  background: #f0f9ff;
}
.lk-da-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.lk-da-badge {
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 3px 8px;
  border-radius: 6px;
  background: #16a34a;
  color: #fff;
}
.lk-da-card.is-draft-dn .lk-da-badge {
  background: #d97706;
}
.lk-da-card.is-despatched .lk-da-badge {
  background: #0369a1;
}
.lk-da-id {
  font-size: 12px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
  color: #334155;
}
.lk-da-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 14px;
}
.lk-da-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.lk-da-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #64748b;
  letter-spacing: 0.03em;
}
.lk-da-val {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  word-break: break-word;
}
.lk-dn-btn {
  align-self: flex-start;
  flex-shrink: 0;
  padding: 8px 14px;
  border-radius: 8px;
  border: none;
  background: #16a34a;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.lk-dn-btn:hover {
  background: #15803d;
}
.lk-dn-btn-done {
  background: #0369a1;
}
.lk-dn-btn-done:hover {
  background: #075985;
}
.lk-da-card.is-club {
  border-color: #0ea5e9;
  background: linear-gradient(180deg, #f0f9ff 0%, #fff 48%);
}
.lk-club-orders {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 8px 0;
}
.lk-club-order {
  display: grid;
  grid-template-columns: 90px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  font-size: 12px;
}
.lk-club-order.is-active {
  border-color: #0ea5e9;
  background: #e0f2fe;
  font-weight: 700;
}
.lk-club-order.is-done {
  opacity: 0.75;
  background: #ecfdf5;
  border-color: #86efac;
}
.lk-club-seq {
  color: #0369a1;
  font-weight: 700;
}
.lk-club-scan {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0;
  align-items: stretch;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  min-width: 0;
}
.lk-club-scan-hint {
  font-size: 11px;
  color: #64748b;
  margin: -2px 0 8px;
}
.lk-dn-btn-cam {
  background: #0ea5e9 !important;
  border-color: #0284c7 !important;
}
.lk-dn-btn-scan {
  background: #0f172a !important;
  border-color: #0f172a !important;
  color: #fff !important;
  font-weight: 700;
  flex: 0 1 auto;
  min-width: 0;
  white-space: nowrap;
}
.lk-club-scan-input {
  flex: 1 1 160px;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.lk-dn-btn-scan {
  background: #0284c7;
}
.lk-dn-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.lk-club-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  width: 100%;
  min-width: 0;
}
.lk-club-actions .lk-dn-btn {
  align-self: stretch;
  width: 100%;
  text-align: center;
  box-sizing: border-box;
}
.lk-club-dn-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.lk-club-dn-item {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: #f0f9ff;
  border-radius: 4px;
  padding: 0 4px;
}
.lk-club-dn-link {
  font-size: 11px;
  font-weight: 600;
  color: #0369a1;
}
.lk-club-dn-del {
  border: none;
  background: transparent;
  color: #b91c1c;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.lk-club-dn-del:hover {
  color: #7f1d1d;
}
.lk-da-date-row {
  margin: 4px 0 8px;
  padding: 0;
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
.lk-da-date-row .lk-da-label {
  flex: 0 0 100%;
}
.lk-da-date-row .lk-da-date-input {
  flex: 0 1 160px;
  width: auto;
  min-width: 140px;
  max-width: 180px;
  height: 32px;
  line-height: 1.2;
  align-self: center;
  box-sizing: border-box;
}
.lk-view-rolls-btn {
  margin-left: auto;
  font-size: 11px !important;
  padding: 4px 8px !important;
  flex: 0 0 auto;
  align-self: center;
  height: 32px;
}
.lk-da-date-input {
  max-width: 180px;
  font-size: 11px;
  padding: 4px 8px;
}
.lk-history-go {
  font-size: 12px;
  font-weight: 700;
  color: #0284c7;
  flex-shrink: 0;
  padding-top: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .lk-gate,
  .lk-truck-transfer,
  .lk-truck-despatch,
  .lk-card-wrap,
  .lk-container {
    animation: none !important;
    transition: none !important;
  }
}
</style>
