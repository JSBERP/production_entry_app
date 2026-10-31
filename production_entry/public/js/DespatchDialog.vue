<template>
  <Teleport to="body">
    <div v-if="modelValue" class="tl-overlay" @click.self="close">
      <div class="tl-dialog" role="dialog" aria-modal="true" aria-labelledby="tl-despatch-title">
      <div class="tl-header">
        <h3 id="tl-despatch-title">Despatch rows</h3>
        <button type="button" class="tl-close" @click="close">✕</button>
      </div>
      <div class="tl-filters">
        <input v-model="dlgClub" type="text" placeholder="Filter Clubbing Sheet ID" @keydown.enter.prevent="applyClubFilter" />
        <input v-model="dlgParty" type="text" placeholder="Filter order code" />
        <input v-model="dlgCustomer" type="text" placeholder="Filter customer" />
        <button type="button" class="cc-clear-btn" :disabled="loading" @click="applyClubFilter">Apply club</button>
        <button type="button" class="cc-clear-btn" :disabled="loading || !hasSelection" @click="clearSelection">Clear selection</button>
        <button type="button" class="cc-clear-btn" :disabled="loading" @click="loadRows">Refresh list</button>
      </div>
      <p v-if="dlgClub.trim()" class="tl-muted tl-club-hint">
        Apply club pre-selects ready rows — uncheck any order you want to leave out before submitting.
      </p>
      <div class="tl-company-row">
        <label>From company</label>
        <select v-model="fromCompany" class="tl-select">
          <option value="">—</option>
          <option v-for="c in companies" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
      </div>
      <p v-if="loading" class="tl-muted">Loading…</p>
      <p v-else-if="!rows.length" class="tl-muted">
        No rows with movement <strong>Despatch</strong> for this table view (SPR submitted). Check month/unit filters or Planning Sheet movement.
      </p>
      <div v-else class="tl-table-wrap">
        <table class="tl-table">
          <thead>
            <tr>
              <th></th>
              <th>Clubbing Sheet ID</th>
              <th>Order Code</th>
              <th>Customer</th>
              <th>Quality</th>
              <th>Colour</th>
              <th>GSM</th>
              <th>Width</th>
              <th>SPR</th>
              <th>Status</th>
              <th>Batches</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filteredRows" :key="rowSelectionId(row)">
              <td>
                <input
                  type="checkbox"
                  :disabled="!isSelected(row) && !row.can_despatch"
                  :checked="isSelected(row)"
                  @change="toggleRow(row, $event)"
                />
              </td>
              <td>{{ row.clubbing_sheet || "—" }}</td>
              <td>{{ row.party_code }}</td>
              <td>{{ row.customer_name }}</td>
              <td>{{ rowAttrs(row).quality }}</td>
              <td>{{ rowAttrs(row).color }}</td>
              <td>{{ rowAttrs(row).gsm }}</td>
              <td>{{ rowAttrs(row).width }}</td>
              <td>{{ row.spr_name || "—" }}</td>
              <td><span :class="statusClass(row)">{{ statusLabel(row) }}</span></td>
              <td class="tl-batch-cell">
                <template v-if="row.can_despatch">
                  <button type="button" class="cc-clear-btn" @click="ensureSelectAndOpenBatches(row)">
                    {{
                      isSelected(row) && batchSummary(row)
                        ? "Edit batches"
                        : isSelected(row)
                          ? "Select batches"
                          : "Select batches"
                    }}
                  </button>
                  <div v-if="batchSummary(row)" class="tl-batch-summary">{{ batchSummary(row) }}</div>
                </template>
                <span v-else class="tl-muted">{{ row.despatch_block_reason || "—" }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="batchPickerOpenFor" class="tl-batch-panel">
        <div class="tl-batch-panel-head">
          <div>
            <strong>Select batches</strong>
            <span class="tl-batch-meta">
              {{ batchPickerRow?.party_code }} · {{ batchPickerRow?.customer_name }}
              <template v-if="batchPickerRow?.spr_name"> · {{ batchPickerRow.spr_name }}</template>
            </span>
            <div v-if="batchPickerRow?._isSprGroup && batchPickerRow.item_codes?.length" class="tl-batch-items">
              Items: {{ batchPickerRow.item_codes.join(", ") }}
            </div>
          </div>
          <div class="tl-batch-head-actions">
            <button type="button" class="cc-clear-btn tl-batch-mini" @click="selectAllBatches">Select all</button>
            <button type="button" class="cc-clear-btn tl-batch-mini" @click="clearAllBatches">Clear</button>
            <label class="tl-batch-toggle">
              <input type="radio" value="spr" v-model="batchSource" @change="reloadBatches" />
              SPR produced
            </label>
            <label class="tl-batch-toggle">
              <input type="radio" value="other" v-model="batchSource" @change="reloadBatches" />
              Other batches
            </label>
            <button type="button" class="tl-close" @click="closeBatchPicker">✕</button>
          </div>
        </div>
        <p v-if="batchLoading" class="tl-muted">Loading batches…</p>
        <p v-else-if="!batchOptions.length" class="tl-block">
          No batches available on this SPR.
          <span v-if="batchEmptyHint" class="tl-muted"><br />{{ batchEmptyHint }}</span>
          Try <strong>Other batches</strong>, or open the SPR and confirm Batch No is filled on produced rolls.
        </p>
        <div v-else class="tl-batch-table-wrap">
          <table class="tl-batch-table">
            <thead>
              <tr>
                <th></th>
                <th>Batch No</th>
                <th>Quality</th>
                <th>Colour</th>
                <th>GSM</th>
                <th>Width</th>
                <th class="text-right">Net / Avail (Kg)</th>
                <th class="text-right">Despatch Qty (Kg)</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="b in batchOptions" :key="b.batch_no" :class="{ 'is-selected': b.selected }" @click="toggleBatchRow(b)">
                <td><input type="checkbox" v-model="b.selected" @click.stop /></td>
                <td class="tl-batch-no">{{ b.batch_no }}</td>
                <td>{{ b.quality || "—" }}</td>
                <td>{{ b.color || "—" }}</td>
                <td>{{ b.gsm || "—" }}</td>
                <td>{{ formatWidth(b.width_inch) }}</td>
                <td class="text-right">{{ formatQty(b.available_qty) }}</td>
                <td class="text-right" @click.stop>
                  <input type="number" class="tl-batch-qty-input" step="0.001" min="0.001" :disabled="!b.selected" v-model.number="b.qty" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="tl-batch-panel-foot">
          <button type="button" class="cc-clear-btn" @click="closeBatchPicker">Cancel</button>
          <button type="button" class="cc-save-arrange-btn" :disabled="!batchApplyEnabled" @click="applyBatches">Apply batches</button>
        </div>
      </div>

      <div class="tl-footer">
        <button type="button" class="cc-clear-btn" @click="close">Cancel</button>
        <button type="button" class="cc-save-arrange-btn" :disabled="submitting || !canSubmit" @click="submit">
          {{ submitting ? "Submitting…" : "Submit for despatch approval" }}
        </button>
      </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import {
  groupRowsBySpr,
  rowSelectionId,
  buildLogisticsSubmitLines,
} from "./logistics_spr_group.js";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  boardKind: { type: String, default: "production" },
  filterContext: { type: Object, default: () => ({}) },
  prefill: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["update:modelValue", "submitted"]);

const API = "production_entry.production_planning.despatch_logistics";
const COMP_API = "production_entry.production_planning.transfer_logistics";

const loading = ref(false);
const submitting = ref(false);
const rows = ref([]);
const companies = ref([]);
const fromCompany = ref("");
const dlgParty = ref("");
const dlgCustomer = ref("");
const dlgClub = ref("");
const selection = ref({});
const batchPickerOpenFor = ref("");
const batchPickerRow = ref(null);
const batchOptions = ref([]);
const batchLoading = ref(false);
const batchSource = ref("spr");
const batchEmptyHint = ref("");

const displayRows = computed(() => groupRowsBySpr(rows.value));

const filteredRows = computed(() => {
  const pc = (dlgParty.value || "").trim().toLowerCase();
  const cu = (dlgCustomer.value || "").trim().toLowerCase();
  const club = (dlgClub.value || "").trim().toLowerCase();
  return displayRows.value.filter((r) => {
    if (pc && !(r.party_code || "").toLowerCase().includes(pc)) return false;
    if (cu && !(r.customer_name || "").toLowerCase().includes(cu)) return false;
    if (club && !(r.clubbing_sheet || "").toLowerCase().includes(club)) return false;
    return true;
  });
});

const batchApplyEnabled = computed(() => {
  const picked = batchOptions.value.filter((b) => b.selected);
  return picked.length > 0 && picked.every((b) => b.batch_no && ltn(b.qty) > 0);
});

const canSubmit = computed(() => {
  if (!(fromCompany.value || "").trim()) return false;
  const entries = Object.values(selection.value);
  if (!entries.length) return false;
  return entries.every((s) => Array.isArray(s.batches) && s.batches.length > 0);
});

const hasSelection = computed(() => Object.keys(selection.value || {}).length > 0);

function ltn(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function formatQty(q) {
  return String(Math.round(ltn(q) * 1000) / 1000);
}

function isSelected(row) {
  return Boolean(selection.value[rowSelectionId(row)]);
}

function batchSummary(row) {
  const sel = selection.value[rowSelectionId(row)];
  if (!sel?.batches?.length) return "";
  return sel.batches.map((b) => `${b.batch_no} · ${formatQty(b.qty)} kg`).join("; ");
}

function statusLabel(row) {
  const st = String(row.despatch_status || "").trim();
  const stLc = st.toLowerCase();
  // Prior lot approved/despatched but remaining rolls can still be picked
  if (row.can_despatch && (stLc === "approved" || stLc === "despatched")) {
    return "Balance ready";
  }
  if (st) return st;
  if (!row.can_despatch) return row.despatch_block_reason || "Blocked";
  return "Ready";
}

function statusClass(row) {
  const st = String(row.despatch_status || "").toLowerCase();
  if (st === "pending approval" || st === "draft") return "tl-warn";
  if (row.can_despatch && (st === "approved" || st === "despatched")) return "tl-ok";
  if (st === "approved" || st === "despatched" || st === "draft dn") return "tl-transferred";
  return row.can_despatch ? "tl-ok" : "tl-block";
}

function toggleRow(row, ev) {
  const id = rowSelectionId(row);
  // Always allow unselect (not locked by Apply club)
  if (!ev.target.checked) {
    const next = { ...selection.value };
    delete next[id];
    selection.value = next;
    if (batchPickerOpenFor.value === id) closeBatchPicker();
    return;
  }
  if (!row.can_despatch) {
    ev.target.checked = false;
    frappe.msgprint(row.despatch_block_reason || "Cannot despatch this row.");
    return;
  }
  selection.value = { ...selection.value, [id]: { row, batches: selection.value[id]?.batches || [] } };
}

function clearSelection() {
  selection.value = {};
  closeBatchPicker();
}

function openBatchPicker(row) {
  batchPickerRow.value = row;
  batchPickerOpenFor.value = rowSelectionId(row);
  batchSource.value = "spr";
  batchEmptyHint.value = "";
  reloadBatches();
}

function ensureSelectAndOpenBatches(row) {
  if (!row.can_despatch) {
    frappe.msgprint(row.despatch_block_reason || "Cannot despatch this row.");
    return;
  }
  const id = rowSelectionId(row);
  if (!selection.value[id]) {
    selection.value = { ...selection.value, [id]: { row, batches: [] } };
  }
  openBatchPicker(row);
}

function reloadBatches() {
  const row = batchPickerRow.value;
  if (!row) return;
  batchLoading.value = true;
  batchOptions.value = [];
  batchEmptyHint.value = "";
  const existing = selection.value[rowSelectionId(row)]?.batches || [];
  const existingMap = {};
  existing.forEach((b) => {
    existingMap[b.batch_no] = b;
  });
  if (batchSource.value === "other" && row._isSprGroup && (row.item_codes || []).length > 1) {
    const itemCodes = row.item_codes || [];
    Promise.all(
      itemCodes.map(
        (ic) =>
          new Promise((resolve) => {
            frappe.call({
              method: `${API}.get_despatch_other_batches`,
              args: {
                item_code: ic,
                from_company: fromCompany.value,
                party_code: row.party_code,
              },
              callback: (r) => resolve(r.message || []),
              error: () => resolve([]),
            });
          })
      )
    ).then((lists) => {
      const seen = new Set();
      const merged = [];
      lists.flat().forEach((b) => {
        const key = `${b.batch_no}::${b.item_code || ""}`;
        if (!b.batch_no || seen.has(key)) return;
        seen.add(key);
        merged.push(b);
      });
      applyLoadedBatches(merged, row, existingMap);
      batchLoading.value = false;
      if (!merged.length) {
        batchEmptyHint.value = `No stock batches for items: ${itemCodes.join(", ")}`;
      }
    });
    return;
  }

  const method =
    batchSource.value === "other"
      ? `${API}.get_despatch_other_batches`
      : `${API}.get_despatch_spr_batches`;
  const args =
    batchSource.value === "other"
      ? {
          item_code: row._isSprGroup ? (row.item_codes || [])[0] || row.item_code : row.item_code,
          from_company: fromCompany.value,
          party_code: row.party_code,
        }
      : {
          spr_name: row.spr_name,
          item_code: row._isSprGroup ? "" : row.item_code || "",
          party_code: row.party_code,
          from_company: fromCompany.value,
        };
  frappe.call({
    method,
    args,
    callback: (r) => {
      const batches = r.message || [];
      applyLoadedBatches(batches, row, existingMap);
      batchLoading.value = false;
      if (!batches.length) {
        batchEmptyHint.value = row.spr_name
          ? `Checked ${row.spr_name} for item ${row.item_code || "—"} / order ${row.party_code || "—"}.`
          : "";
      }
    },
    error: (err) => {
      batchLoading.value = false;
      batchEmptyHint.value = (err && err.message) || "Server error while loading batches.";
      frappe.msgprint({
        title: "Batch load failed",
        indicator: "red",
        message: batchEmptyHint.value,
      });
    },
  });
}

function applyLoadedBatches(batches, row, existingMap) {
  batchOptions.value = (batches || []).map((b) => {
    const prev = existingMap[b.batch_no];
    const avail = ltn(b.available_qty || b.qty) || 1;
    return {
      batch_no: b.batch_no,
      item_code: b.item_code || row.item_code,
      quality: b.quality || "",
      color: b.color || "",
      gsm: b.gsm || "",
      width_inch: b.width_inch || 0,
      available_qty: avail,
      net_weight: ltn(b.net_weight || b.qty),
      qty: prev ? ltn(prev.qty) : avail,
      selected: Boolean(prev),
    };
  });
}

function selectAllBatches() {
  batchOptions.value.forEach((b) => {
    b.selected = true;
    if (ltn(b.qty) <= 0) b.qty = b.available_qty || 1;
  });
}

function clearAllBatches() {
  batchOptions.value.forEach((b) => {
    b.selected = false;
  });
}

function formatWidth(w) {
  const n = ltn(w);
  return n > 0 ? String(n) : "—";
}

function rowAttrs(row) {
  const batches = selection.value[rowSelectionId(row)]?.batches || [];
  const first = batches[0] || row || {};
  return {
    quality: first.quality || row.quality || "—",
    color: first.color || row.color || "—",
    gsm: first.gsm || row.gsm || "—",
    width: formatWidth(first.width_inch || row.width_inch),
  };
}

function closeBatchPicker() {
  batchPickerOpenFor.value = "";
  batchPickerRow.value = null;
  batchOptions.value = [];
}

function toggleBatchRow(b) {
  b.selected = !b.selected;
  if (b.selected && ltn(b.qty) <= 0) b.qty = b.available_qty || 1;
}

function applyBatches() {
  const row = batchPickerRow.value;
  if (!row) return;
  const id = rowSelectionId(row);
  const picked = batchOptions.value
    .filter((b) => b.selected && b.batch_no && ltn(b.qty) > 0)
    .map((b) => ({
      batch_no: b.batch_no,
      qty: ltn(b.qty),
      net_weight: ltn(b.net_weight || b.qty),
      item_code: b.item_code || row.item_code,
      quality: b.quality || "",
      color: b.color || "",
      gsm: b.gsm || "",
      width_inch: b.width_inch || 0,
    }));
  if (!picked.length) {
    frappe.msgprint("Select at least one batch.");
    return;
  }
  selection.value = { ...selection.value, [id]: { row, batches: picked } };
  closeBatchPicker();
}

function close() {
  closeBatchPicker();
  emit("update:modelValue", false);
}

function loadRows(opts = {}) {
  loading.value = true;
  const ctx = props.filterContext || {};
  const autoClub = opts.autoSelectClub ? (dlgClub.value || "").trim() : "";
  frappe.call({
    method: `${API}.get_despatch_eligible_rows`,
    args: {
      board_kind: props.boardKind,
      board_slug: ctx.board_slug || "",
      view_scope: ctx.view_scope || "daily",
      date: ctx.date || "",
      week: ctx.week || "",
      month: ctx.month || "",
      unit: ctx.unit || "",
      party_code: dlgParty.value || ctx.party_code || "",
      customer: dlgCustomer.value || ctx.customer || "",
      clubbing_sheet: dlgClub.value || "",
      from_company: fromCompany.value,
    },
    callback: (r) => {
      rows.value = r.message || [];
      loading.value = false;
      if (autoClub) autoSelectForClub(autoClub);
    },
    error: () => {
      loading.value = false;
    },
  });
}

function autoSelectForClub(club) {
  const clubLc = (club || "").trim().toLowerCase();
  if (!clubLc) return;
  // Fresh starting set for this club — user may uncheck any order afterward
  const next = {};
  groupRowsBySpr(rows.value).forEach((row) => {
    if (!row.can_despatch) return;
    if (!(row.clubbing_sheet || "").toLowerCase().includes(clubLc)) return;
    const id = rowSelectionId(row);
    const prevBatches = selection.value[id]?.batches || [];
    next[id] = { row, batches: prevBatches };
  });
  selection.value = next;
  const n = Object.keys(next).length;
  if (n) {
    frappe.show_alert({
      message: __(
        "Pre-selected {0} row(s) for club {1}. Uncheck any order to leave it out, then pick batches.",
        [String(n), club]
      ),
      indicator: "blue",
    });
  }
}

function applyClubFilter() {
  loadRows({ autoSelectClub: true });
}

function submit() {
  if (!fromCompany.value) {
    frappe.msgprint("Select from company.");
    return;
  }
  const lines = buildLogisticsSubmitLines(selection.value, "despatch");
  if (!lines.length) {
    frappe.msgprint("Select rows and batches.");
    return;
  }
  submitting.value = true;
  frappe.call({
    method: `${API}.create_despatch_approval_request`,
    args: { from_company: fromCompany.value, lines: JSON.stringify(lines) },
    callback: (r) => {
      submitting.value = false;
      frappe.show_alert({ message: `Despatch approval ${r.message?.name}`, indicator: "green" });
      emit("submitted", r.message);
      close();
    },
    error: () => {
      submitting.value = false;
    },
  });
}

async function loadCompanies() {
  const r = await frappe.call({ method: `${COMP_API}.get_logistics_companies` });
  companies.value = r.message || [];
  if (!fromCompany.value && companies.value.length) {
    const pref = props.prefill?.from_company;
    fromCompany.value =
      pref && companies.value.some((c) => c.name === pref)
        ? pref
        : companies.value.find((c) => c.name.includes("Jayashree"))?.name || companies.value[0].name;
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    selection.value = {};
    const ctx = props.filterContext || {};
    dlgParty.value = props.prefill?.party_code || ctx.party_code || "";
    dlgCustomer.value = props.prefill?.customer || ctx.customer || "";
    dlgClub.value = props.prefill?.clubbing_sheet || "";
    fromCompany.value = props.prefill?.from_company || "";
    loadCompanies();
    loadRows();
  }
);

watch(fromCompany, () => {
  if (props.modelValue) loadRows();
});
</script>

