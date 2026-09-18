<template>
  <div class="co-container">
    <!-- Hero -->
    <div class="co-hero">
      <div class="co-hero-text">
        <h2 class="co-title">Confirm Orders</h2>
        <p class="co-subtitle">Planning sheets grouped by Sales Order / Planning sheet company</p>
      </div>
      <div class="co-hero-stats">
        <div class="co-hero-stat">
          <span class="co-hero-stat-value">{{ totalSheets }}</span>
          <span class="co-hero-stat-label">Sheets</span>
        </div>
        <div v-if="totalQtyKg > 0" class="co-hero-stat">
          <span class="co-hero-stat-value">{{ (totalQtyKg / 1000).toFixed(2) }} T</span>
          <span class="co-hero-stat-label">FG Kg</span>
        </div>
        <div v-if="totalQtyPcs > 0" class="co-hero-stat">
          <span class="co-hero-stat-value">{{ formatQty(totalQtyPcs) }} Pcs</span>
          <span class="co-hero-stat-label">FG Pcs</span>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="co-filters">
      <div class="co-filter-item">
        <label>View Scope</label>
        <select v-model="viewScope" @change="onScopeChange">
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="all">All dates</option>
        </select>
      </div>
      <div class="co-filter-item" v-if="viewScope === 'daily'">
        <label>Planned Date</label>
        <input type="date" v-model="filterDate" @change="fetchData" />
      </div>
      <div class="co-filter-item" v-else-if="viewScope === 'weekly'">
        <label>Select Week</label>
        <input type="week" v-model="filterWeek" @change="fetchData" />
      </div>
      <div class="co-filter-item" v-else-if="viewScope === 'monthly'">
        <label>Select Month</label>
        <input type="month" v-model="filterMonth" @change="fetchData" />
      </div>
      <div class="co-filter-item">
        <label>Order Code</label>
        <input type="text" v-model="filterOrderCode" placeholder="Search order..." @input="debouncedFetch" />
      </div>
      <div class="co-filter-item">
        <label>Customer</label>
        <input type="text" v-model="filterCustomer" placeholder="Search customer..." @input="debouncedFetch" />
      </div>
      <div class="co-filter-item">
        <label>Unit</label>
        <select v-model="filterUnit" @change="fetchData">
          <option value="">All Units</option>
          <option v-for="u in unitOptions" :key="u" :value="u">{{ u }}</option>
        </select>
      </div>
      <button class="co-clear-btn" @click="clearFilters">✕ Clear</button>
      <button class="co-clear-btn" @click="fetchData">🔄 Refresh</button>
      <label class="co-toggle-empty">
        <input type="checkbox" v-model="hideEmpty" />
        Hide empty companies
      </label>
    </div>

    <!-- Board: one card per company -->
    <div v-if="loading" class="co-loading">Loading confirm orders…</div>
    <div v-else class="co-board">
      <div
        v-for="(card, idx) in visibleCards"
        :key="card.company"
        class="co-company-card"
        :style="{ animationDelay: `${Math.min(idx * 50, 400)}ms` }"
      >
        <div class="co-card-header" :style="{ borderTopColor: cardColor(idx) }">
          <div class="co-card-company">
            <span class="co-card-icon">🏭</span>
            <span class="co-card-name">{{ card.company }}</span>
          </div>
          <div class="co-card-meta">
            <span class="co-card-count">{{ card.sheets.length }} sheet{{ card.sheets.length === 1 ? '' : 's' }}</span>
            <span v-if="(card.total_qty_kg || 0) > 0" class="co-card-qty">{{ ((card.total_qty_kg || 0) / 1000).toFixed(2) }} T</span>
            <span v-if="(card.total_qty_pcs || 0) > 0" class="co-card-qty co-card-qty-pcs">{{ formatQty(card.total_qty_pcs) }} Pcs</span>
          </div>
        </div>

        <div class="co-card-body">
          <div
            v-for="sheet in card.sheets"
            :key="sheet.name"
            class="co-sheet-chip"
            @click="openSheet(sheet.name)"
          >
            <div class="co-sheet-top">
              <span class="co-sheet-order">{{ sheet.orderCode || sheet.name }}</span>
              <span class="co-sheet-status" :class="statusClass(sheet.planningStatus)">{{ sheet.planningStatus }}</span>
            </div>
            <div class="co-sheet-customer">{{ sheet.customerName || sheet.customer }}</div>
            <div class="co-sheet-meta">
              <span class="co-sheet-qty">{{ sheet.fg_display || '—' }}</span>
              <span v-if="sheet.parent_fabric" class="co-sheet-fg-type">{{ sheet.parent_fabric }}</span>
              <span v-if="sheet.units" class="co-sheet-units">· {{ sheet.units }}</span>
            </div>
            <div class="co-sheet-dates">
              <span v-if="sheet.plannedDate">📅 {{ formatDate(sheet.plannedDate) }}</span>
              <span v-if="sheet.dod">🚚 DOD {{ formatDate(sheet.dod) }}</span>
            </div>
            <div class="co-sheet-foot">
              <span class="co-sheet-ps">{{ sheet.name }}</span>
              <span class="co-sheet-open">Open →</span>
            </div>
          </div>

          <div v-if="!card.sheets.length" class="co-empty">No confirmed orders</div>
        </div>
      </div>

      <div v-if="!visibleCards.length" class="co-empty co-empty-board">No company cards to show.</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";

const CARD_COLORS = [
  "#4f46e5", "#0891b2", "#16a34a", "#d97706", "#dc2626",
  "#7c3aed", "#0d9488", "#be185d", "#65a30d", "#475569",
];

const viewScope = ref("all");
const filterDate = ref(frappe.datetime.get_today());
const filterWeek = ref("");
const filterMonth = ref("");
const filterOrderCode = ref("");
const filterCustomer = ref("");
const filterUnit = ref("");
const hideEmpty = ref(false);

const loading = ref(false);
const cards = ref([]);
const unitOptions = ref([]);

let debounceTimer = null;
function debouncedFetch() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchData, 300);
}

const visibleCards = computed(() =>
  hideEmpty.value ? cards.value.filter((c) => c.sheets.length) : cards.value
);

const totalSheets = computed(() =>
  cards.value.reduce((s, c) => s + c.sheets.length, 0)
);
const totalQtyKg = computed(() =>
  cards.value.reduce((s, c) => s + (c.total_qty_kg || 0), 0)
);
const totalQtyPcs = computed(() =>
  cards.value.reduce((s, c) => s + (c.total_qty_pcs || 0), 0)
);

function formatQty(n) {
  const v = Number(n) || 0;
  if (Math.abs(v - Math.round(v)) < 1e-6) {
    return Math.round(v).toLocaleString("en-IN");
  }
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function cardColor(idx) {
  return CARD_COLORS[idx % CARD_COLORS.length];
}

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "draft") return "is-draft";
  if (s === "finalized") return "is-finalized";
  if (s === "in production") return "is-production";
  if (s === "completed") return "is-completed";
  return "is-other";
}

function formatDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr;
  return `${String(d.getDate()).padStart(2, "0")}-${String(d.getMonth() + 1).padStart(2, "0")}-${d.getFullYear()}`;
}

function onScopeChange() {
  if (viewScope.value === "monthly" && !filterMonth.value) {
    filterMonth.value = frappe.datetime.get_today().substring(0, 7);
  } else if (viewScope.value === "weekly" && !filterWeek.value) {
    const d = new Date();
    const dStart = new Date(d.getFullYear(), 0, 1);
    const days = Math.floor((d - dStart) / (24 * 60 * 60 * 1000));
    const weekNum = Math.ceil(days / 7);
    filterWeek.value = `${d.getFullYear()}-W${String(weekNum).padStart(2, "0")}`;
  } else if (viewScope.value === "daily" && !filterDate.value) {
    filterDate.value = frappe.datetime.get_today();
  }
  fetchData();
}

function clearFilters() {
  viewScope.value = "all";
  filterDate.value = frappe.datetime.get_today();
  filterWeek.value = "";
  filterMonth.value = "";
  filterOrderCode.value = "";
  filterCustomer.value = "";
  filterUnit.value = "";
  fetchData();
}

function openSheet(name) {
  frappe.set_route("Form", "Planning sheet", name);
}

function buildDateArgs() {
  const args = {};
  if (viewScope.value === "daily") {
    if (filterDate.value) args.order_date = filterDate.value;
  } else if (viewScope.value === "weekly" && filterWeek.value) {
    const [yearStr, weekStr] = filterWeek.value.split("-W");
    const y = parseInt(yearStr);
    const w = parseInt(weekStr);
    const simple = new Date(y, 0, 1 + (w - 1) * 7);
    const dow = simple.getDay();
    const start = new Date(simple);
    if (dow <= 4) start.setDate(simple.getDate() - simple.getDay() + 1);
    else start.setDate(simple.getDate() + 8 - simple.getDay());
    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    const fmt = (d) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    args.start_date = fmt(start);
    args.end_date = fmt(end);
  } else if (viewScope.value === "monthly" && filterMonth.value) {
    const [year, month] = filterMonth.value.split("-");
    const lastDay = new Date(year, month, 0).getDate();
    args.start_date = `${filterMonth.value}-01`;
    args.end_date = `${filterMonth.value}-${String(lastDay).padStart(2, "0")}`;
  }
  return args;
}

async function fetchData() {
  loading.value = true;
  try {
    const args = buildDateArgs();
    if (filterOrderCode.value) args.order_code = filterOrderCode.value;
    if (filterCustomer.value) args.customer = filterCustomer.value;
    if (filterUnit.value) args.unit = filterUnit.value;

    const r = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_confirm_orders_company_kanban",
      args,
    });
    const msg = r.message || {};
    cards.value = msg.companies || [];
    if (Array.isArray(msg.unitOptions)) {
      unitOptions.value = msg.unitOptions;
    }
  } catch (e) {
    console.error("Confirm Orders kanban load failed:", e);
    frappe.msgprint("Error loading Confirm Orders");
  } finally {
    loading.value = false;
  }
}

async function loadUnitOptions() {
  try {
    const r = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_confirm_orders_unit_options",
    });
    if (Array.isArray(r.message)) {
      unitOptions.value = r.message;
    }
  } catch (e) {
    console.warn("Confirm Orders unit options load failed:", e);
  }
}

onMounted(() => {
  loadUnitOptions();
  fetchData();
});
</script>

<style scoped>
.co-container {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: #f3f4f6;
  font-family: "Inter", sans-serif;
}

/* Hero */
.co-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 22px;
  background: linear-gradient(120deg, #1e293b 0%, #312e81 60%, #4f46e5 100%);
  color: white;
}
.co-hero-text {
  color: #ffffff;
}
.co-title {
  margin: 0;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 0.2px;
  color: #ffffff;
}
.co-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  opacity: 0.85;
}
.co-hero-stats {
  display: flex;
  gap: 12px;
}
.co-hero-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  padding: 8px 16px;
  min-width: 90px;
}
.co-hero-stat-value {
  font-size: 16px;
  font-weight: 800;
}
.co-hero-stat-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  opacity: 0.8;
}

/* Filters */
.co-filters {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
  background: white;
  border-bottom: 1px solid #e5e7eb;
}
.co-filter-item {
  display: flex;
  flex-direction: column;
}
.co-filter-item label {
  font-size: 11px;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 2px;
}
.co-filter-item input,
.co-filter-item select {
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
  min-width: 130px;
}
.co-clear-btn {
  padding: 6px 12px;
  background: white;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  color: #374151;
}
.co-clear-btn:hover {
  background: #f9fafb;
}
.co-toggle-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #374151;
  margin: 0 0 6px;
  cursor: pointer;
  user-select: none;
}

/* Board */
.co-loading {
  padding: 32px;
  text-align: center;
  color: #6b7280;
  font-weight: 600;
}
.co-board {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 16px;
  padding: 16px;
  align-items: start;
}
.co-company-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  animation: co-card-in 0.35s ease both;
  display: flex;
  flex-direction: column;
}
@keyframes co-card-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.co-card-header {
  padding: 12px 14px;
  border-top: 4px solid #4f46e5;
  border-bottom: 1px solid #f3f4f6;
  background: #fafafa;
}
.co-card-company {
  display: flex;
  align-items: center;
  gap: 8px;
}
.co-card-icon {
  font-size: 16px;
}
.co-card-name {
  font-weight: 800;
  font-size: 13.5px;
  color: #111827;
  line-height: 1.3;
}
.co-card-meta {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}
.co-card-count {
  font-size: 11px;
  font-weight: 700;
  color: #4f46e5;
  background: #eef2ff;
  border-radius: 999px;
  padding: 2px 8px;
}
.co-card-qty {
  font-size: 11px;
  font-weight: 700;
  color: #065f46;
  background: #ecfdf5;
  border-radius: 999px;
  padding: 2px 8px;
}
.co-card-qty-pcs {
  color: #1e40af;
  background: #dbeafe;
}

.co-card-body {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
  background: #f9fafb;
  flex: 1;
}

/* Sheet chips */
.co-sheet-chip {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
  transition: transform 0.1s, box-shadow 0.1s, border-color 0.1s;
}
.co-sheet-chip:hover {
  transform: translateY(-1px);
  border-color: #a5b4fc;
  box-shadow: 0 4px 8px rgba(79, 70, 229, 0.08);
}
.co-sheet-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.co-sheet-order {
  font-weight: 800;
  font-size: 13px;
  color: #111827;
}
.co-sheet-status {
  font-size: 9.5px;
  font-weight: 700;
  text-transform: uppercase;
  border-radius: 999px;
  padding: 2px 8px;
  white-space: nowrap;
}
.co-sheet-status.is-draft {
  background: #fef3c7;
  color: #92400e;
}
.co-sheet-status.is-finalized {
  background: #dbeafe;
  color: #1e40af;
}
.co-sheet-status.is-production {
  background: #ede9fe;
  color: #5b21b6;
}
.co-sheet-status.is-completed {
  background: #dcfce7;
  color: #166534;
}
.co-sheet-status.is-other {
  background: #f3f4f6;
  color: #374151;
}
.co-sheet-customer {
  font-size: 11.5px;
  color: #4b5563;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.co-sheet-meta {
  font-size: 11px;
  color: #111827;
  margin-top: 2px;
}
.co-sheet-qty {
  font-weight: 700;
}
.co-sheet-fg-type {
  display: inline-block;
  margin-left: 6px;
  font-size: 9.5px;
  font-weight: 700;
  color: #5b21b6;
  background: #ede9fe;
  border-radius: 999px;
  padding: 1px 7px;
  vertical-align: middle;
}
.co-sheet-units {
  color: #6b7280;
}
.co-sheet-dates {
  display: flex;
  gap: 10px;
  font-size: 10.5px;
  color: #6b7280;
  margin-top: 2px;
  flex-wrap: wrap;
}
.co-sheet-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  border-top: 1px dashed #e5e7eb;
  padding-top: 4px;
}
.co-sheet-ps {
  font-size: 10px;
  color: #9ca3af;
  font-family: monospace;
}
.co-sheet-open {
  font-size: 10.5px;
  font-weight: 700;
  color: #4f46e5;
}

.co-empty {
  text-align: center;
  color: #9ca3af;
  font-size: 12.5px;
  font-style: italic;
  padding: 16px 0;
}
.co-empty-board {
  grid-column: 1 / -1;
  padding: 40px 0;
}
</style>
