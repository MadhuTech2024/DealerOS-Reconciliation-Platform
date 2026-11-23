import { useEffect, useState } from "react";
import { getDisagreements, getSummary } from "./api.js";

const REASONS = [
  ["MISSING_IN_B", "Missing in System B"],
  ["ORPHAN_IN_B", "Orphan in System B"],
  ["DUPLICATE_IN_B", "Duplicate in System B"],
  ["VALUE_MISMATCH", "Value mismatch"],
  ["BASE_VALUE_RECORDED", "Base value recorded"],
  ["BLANK_VALUE", "Blank value"],
  ["LOCATION_MISMATCH", "Location mismatch"],
  ["DATE_MISMATCH", "Date mismatch"],
  ["VOIDED_RECORD", "Voided record"],
  ["INVALID_REFERENCE", "Invalid reference"],
];

const SORT_OPTIONS = [
  ["a_value", "System A value: low to high"],
  ["-a_value", "System A value: high to low"],
  ["b_value", "System B value: low to high"],
  ["-b_value", "System B value: high to low"],
  ["difference", "Difference: low to high"],
  ["-difference", "Difference: high to low"],
];

const SUMMARY_CARDS = [
  ["total_disagreements", "Total disagreements", "card-total"],
  ["missing_in_b", "Missing in B", "card-missing"],
  ["orphan_in_b", "Orphan in B", "card-orphan"],
  ["duplicate_in_b", "Duplicates", "card-duplicate"],
  ["value_mismatches", "Value mismatches", "card-value"],
  ["location_mismatches", "Location mismatches", "card-location"],
  ["date_mismatches", "Date mismatches", "card-date"],
];

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return "Unknown";
  const text = String(value);
  const match = text.match(/^(-?)(\d+)(?:\.(\d+))?$/);
  if (!match) return "Unknown";
  const [, sign, whole, fraction = ""] = match;
  const leading = whole.length > 3 ? whole.slice(0, -3) : "";
  const groupedLeading = leading.replace(/\B(?=(\d{2})+$)/g, ",");
  const groupedWhole = leading ? `${groupedLeading},${whole.slice(-3)}` : whole;
  return `${sign ? "-" : ""}₹${groupedWhole}.${fraction.padEnd(2, "0")}`;
}

function formatReason(reason) {
  return REASONS.find(([value]) => value === reason)?.[1] || reason.replaceAll("_", " ");
}

function SortButton({ field, label, sort, onSort }) {
  const activeField = sort?.replace(/^-/, "");
  const active = activeField === field || (field === "a_value" && activeField === "value");
  const descending = sort?.startsWith("-");
  const indicator = active ? (descending ? " ↓" : " ↑") : "";

  return (
    <button className="sort-button" type="button" onClick={() => onSort(field)}>
      {label}<span aria-hidden="true">{indicator}</span>
    </button>
  );
}

export default function App() {
  const [org, setOrg] = useState("ORG-A");
  const [reason, setReason] = useState("");
  const [sort, setSort] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");

    Promise.all([
      getDisagreements({ org, reason, sort }, controller.signal),
      getSummary(org, controller.signal),
    ])
      .then(([disagreements, nextSummary]) => {
        setRows(disagreements.results);
        setSummary(nextSummary);
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          setRows([]);
          setSummary(null);
          setError(requestError.message);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [org, reason, sort, refreshKey]);

  function updateSort(field) {
    setSort((current) => {
      const currentField = current.replace(/^-/, "");
      if (currentField !== field) return field;
      return current.startsWith("-") ? field : `-${field}`;
    });
  }

  function clearFilters() {
    setReason("");
    setSort("");
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">D</div>
          <div>
            <p className="eyebrow">FINANCE OPERATIONS</p>
            <h1>DealerOS <span>Reconciliation</span></h1>
          </div>
        </div>
        <div className="header-context">
          <span className="status-dot" aria-hidden="true" />
          <span>Assessment workspace</span>
          <span className="header-divider" aria-hidden="true" />
          <label htmlFor="org-select">Organization</label>
          <select id="org-select" value={org} onChange={(event) => setOrg(event.target.value)}>
            <option value="ORG-A">ORG-A</option>
            <option value="ORG-B">ORG-B</option>
          </select>
        </div>
      </header>

      <section className="page-intro">
        <div>
          <p className="eyebrow">RECONCILIATION OVERVIEW</p>
          <h2>Transaction exceptions</h2>
          <p className="intro-copy">
            Compare operational transactions with accounting entries and resolve exceptions.
          </p>
        </div>
        <div className="selected-organization">
          <span className="selected-label">VIEWING ORGANIZATION</span>
          <strong>{org}</strong>
        </div>
      </section>

      <section className="summary-grid" aria-label="Organization reconciliation summary">
        {SUMMARY_CARDS.map(([key, label, cardClass]) => (
          <article className={`summary-card ${cardClass}`} key={key}>
            <span className="summary-label">{label}</span>
            <strong className="summary-value">
              {loading ? <span className="skeleton-number" aria-label="Loading" /> : summary?.[key] ?? "—"}
            </strong>
            <span className="summary-org">{org} · scoped</span>
          </article>
        ))}
      </section>

      <section className="reconciliation-panel" aria-labelledby="results-heading">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">REVIEW QUEUE</p>
            <h2 id="results-heading">Disagreements</h2>
            <p className="panel-copy">Exceptions found between System A and System B.</p>
          </div>
          <button
            className="refresh-button"
            type="button"
            onClick={() => setRefreshKey((key) => key + 1)}
            disabled={loading}
          >
            <span aria-hidden="true">↻</span> Refresh
          </button>
        </div>

        <div className="filter-bar">
          <div className="filter-control">
            <label htmlFor="reason-filter">Reason</label>
            <select id="reason-filter" value={reason} onChange={(event) => setReason(event.target.value)}>
              <option value="">All reasons</option>
              {REASONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </div>
          <div className="filter-control">
            <label htmlFor="sort-filter">Sort by</label>
            <select id="sort-filter" value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="">Default order</option>
              {SORT_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </div>
          <div className="filter-actions">
            <span className="result-count">
              {loading ? "Loading results…" : `${rows.length} ${rows.length === 1 ? "result" : "results"}`}
            </span>
            <button className="clear-button" type="button" onClick={clearFilters} disabled={!reason && !sort}>
              Clear filters
            </button>
          </div>
        </div>

        {error && (
          <div className="state-message error-state" role="alert">
            <strong>Could not load reconciliation data</strong>
            <span>{error}</span>
            <button type="button" onClick={() => setRefreshKey((key) => key + 1)}>Try again</button>
          </div>
        )}

        {!error && loading && (
          <div className="state-message loading-state" role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            Loading {org} reconciliation data…
          </div>
        )}

        {!error && !loading && rows.length === 0 && (
          <div className="state-message empty-state">
            <span className="empty-icon" aria-hidden="true">✓</span>
            <strong>No disagreements found</strong>
            <span>{reason ? "Try another reason or clear the filter." : `There are no exceptions for ${org}.`}</span>
          </div>
        )}

        {!error && !loading && rows.length > 0 && (
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Reconciliation disagreements for {org}</caption>
              <thead>
                <tr>
                  <th scope="col">Reason</th>
                  <th scope="col">Record ID</th>
                  <th scope="col">Location</th>
                  <th scope="col" aria-sort={sort === "a_value" || sort === "value" ? "ascending" : sort === "-a_value" || sort === "-value" ? "descending" : "none"}>
                    <SortButton field="a_value" label="System A Value" sort={sort} onSort={updateSort} />
                  </th>
                  <th scope="col" aria-sort={sort === "b_value" ? "ascending" : sort === "-b_value" ? "descending" : "none"}>
                    <SortButton field="b_value" label="System B Value" sort={sort} onSort={updateSort} />
                  </th>
                  <th scope="col" aria-sort={sort === "difference" ? "ascending" : sort === "-difference" ? "descending" : "none"}>
                    <SortButton field="difference" label="Difference" sort={sort} onSort={updateSort} />
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={`${row.reason}-${row.record_id}-${index}`}>
                    <td data-label="Reason">
                      <span className={`reason-badge reason-${row.reason.toLowerCase().replaceAll("_", "-")}`}>
                        {formatReason(row.reason)}
                      </span>
                    </td>
                    <td data-label="Record ID"><span className="record-id">{row.record_id}</span></td>
                    <td data-label="Location">{row.location || ""}</td>
                    <td data-label="System A Value" className="amount-cell">{formatMoney(row.a_value)}</td>
                    <td data-label="System B Value" className="amount-cell">{formatMoney(row.b_value)}</td>
                    <td data-label="Difference" className="amount-cell difference-cell">{formatMoney(row.difference)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <footer className="app-footer">
        <span>DealerOS Financial Reconciliation</span>
        <span>Organization selector is an assessment filter, not authenticated access control.</span>
      </footer>
    </main>
  );
}
