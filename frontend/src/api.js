const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api"
).replace(/\/+$/, "");

async function getJson(path, signal) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw error;
    }
    throw new Error("Unable to reach the reconciliation API. Check that the backend is running.");
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(body?.message || `The API request failed (${response.status}).`);
  }
  if (!body || typeof body !== "object") {
    throw new Error("The reconciliation API returned an invalid response.");
  }
  return body;
}

export function getDisagreements({ org, reason, sort }, signal) {
  const params = new URLSearchParams({ org });
  if (reason) params.set("reason", reason);
  if (sort) params.set("sort", sort);
  return getJson(`/disagreements/?${params.toString()}`, signal);
}

export function getSummary(org, signal) {
  const params = new URLSearchParams({ org });
  return getJson(`/summary/?${params.toString()}`, signal);
}
