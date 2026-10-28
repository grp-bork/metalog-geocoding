/*
 * The browser form for the geocoding service: a plain client of POST /geocode
 * and POST /check, with every vocabulary fetched from GET /options. The only
 * logic of its own is parsing the pasted text into coordinates.
 */

const $ = (id) => document.getElementById(id);

const GRADE_COLOURS = {
  ok: "#1a7f37",
  error: "#c0392b",
  uncheckable: "#8a6d1f",
};
const TYPE_COLOURS = {
  land: "#2f6fb5",
  sea: "#157f86",
  unknown: "#a04a1a",
};

/* ---------------------------------------------------------------- map ---- */

const map = L.map("map", { worldCopyJump: true }).setView([20, 0], 1);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const drawn = L.layerGroup().addTo(map);

// Clicking the map appends a coordinate rather than replacing the box.
map.on("click", (event) => {
  const lat = event.latlng.lat.toFixed(4);
  const lon = ((((event.latlng.lng + 180) % 360) + 360) % 360 - 180).toFixed(4);
  const pair = $("order").value === "lonlat" ? `${lon}\t${lat}` : `${lat}\t${lon}`;
  const box = $("coords");
  box.value = box.value.replace(/\s*$/, "") + (box.value.trim() ? "\n" : "") + pair + "\n";
});

/* ------------------------------------------------------------ options ---- */

async function loadOptions() {
  const health = await fetch("/health").then((r) => r.json());
  $("status").textContent = `v${health.version} · ${health.vocabulary_size ?? "no"} marine names`;

  const options = await fetch("/options").then((r) => r.json());

  const places = $("places");
  for (const name of [...options.seas, ...options.countries]) {
    places.appendChild(new Option(name, name));
  }

  const resolutions = $("location_resolution");
  const notes = { full: "full hierarchy", truncated: "truncated", none: "no hierarchy rows" };
  for (const entry of options.location_resolutions) {
    resolutions.appendChild(new Option(`${entry.value} — ${notes[entry.hierarchy]}`, entry.value));
  }

  const artificials = $("artificials");
  for (const value of options.artificial_no_location) {
    artificials.appendChild(new Option(value, value));
  }
  $("artificial-hint").textContent =
    `Only ${options.artificial_no_location.join(" and ")} change anything: ` +
    "they have no field location, so their coordinate contributes no hierarchy rows.";
}

/* ------------------------------------------------------------- parsing --- */

const NUMBER = String.raw`[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?`;
const ROW = new RegExp(`^(${NUMBER})[\\s,;]+(${NUMBER})\\s*[,;\\t]?\\s*(.*)$`);

/** Read the textarea into one entry per non-comment line. */
function parseRows(text, lonFirst) {
  const rows = [];
  text.split(/\r?\n/).forEach((line, index) => {
    const raw = line.trim();
    if (!raw || raw.startsWith("#")) return;
    const match = raw.match(ROW);
    if (!match) {
      rows.push({ line: index + 1, raw, error: "could not read two numbers" });
      return;
    }
    const first = parseFloat(match[1]);
    const second = parseFloat(match[2]);
    rows.push({
      line: index + 1,
      raw,
      latitude: lonFirst ? second : first,
      longitude: lonFirst ? first : second,
      // Trailing text is this row's own claim, so a pasted list of named
      // sites can be checked in one go without retyping the names.
      label: match[3].trim(),
    });
  });
  return rows;
}

/* ------------------------------------------------------------ requests --- */

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `${url} returned ${response.status}`);
  }
  return payload;
}

/* ----------------------------------------------------------- rendering --- */

function table(node, columns, rows, emptyMessage) {
  node.innerHTML = "";
  if (!rows.length) {
    node.innerHTML = `<tr><td class="empty">${emptyMessage}</td></tr>`;
    return;
  }
  const head = node.insertRow();
  for (const column of columns) {
    const th = document.createElement("th");
    th.textContent = column;
    head.appendChild(th);
  }
  for (const row of rows) {
    const tr = node.insertRow();
    for (const cell of row) {
      const td = tr.insertCell();
      if (cell && typeof cell === "object") {
        td.textContent = cell.text ?? "";
        if (cell.className) td.className = cell.className;
      } else {
        td.textContent = cell ?? "";
      }
    }
  }
}

/** The name a result resolved to, and the context it sits in. */
function describe(result) {
  if (!result || result.type === "unknown") return { name: "—", context: "", extra: "" };
  if (result.type === "land") {
    return {
      name: result.country || "—",
      context: [result.name_1, result.continent].filter(Boolean).join(" · "),
      extra: [result.country_code, result.iso_3166_2].filter(Boolean).join(" · "),
    };
  }
  return {
    name: result.sea || "—",
    context: result.ocean || "",
    extra: [
      result.mrgid ? `MRGID ${result.mrgid}` : "",
      result.longhurst_code ? `${result.longhurst_code} (${result.longhurst_biome})` : "",
    ]
      .filter(Boolean)
      .join(" · "),
  };
}

function fallbackText(result) {
  if (!result || !result.is_fallback) return "";
  const metres = result.fallback_distance_m;
  return metres == null ? "yes" : `${(metres / 1000).toFixed(2)} km`;
}

function key(lat, lon) {
  return `${lat},${lon}`;
}

function renderGeocode(entries, rows) {
  const byLine = new Map(rows.map((row) => [key(row.latitude, row.longitude), row.line]));
  table(
    $("results"),
    ["#", "latitude", "longitude", "lookup", "type", "name", "context", "detail", "fallback"],
    entries.map((entry) => {
      const described = describe(entry.result);
      return [
        byLine.get(key(entry.latitude, entry.longitude)) ?? "",
        { text: entry.latitude, className: "num" },
        { text: entry.longitude, className: "num" },
        entry.requested_type,
        entry.error ? "invalid" : entry.result.type,
        entry.error ? entry.error : described.name,
        described.context,
        described.extra,
        fallbackText(entry.result),
      ];
    }),
    "Nothing geocoded yet.",
  );
}

function renderVerdicts(verdicts, rows) {
  const byId = new Map(rows.map((row) => [`row ${row.line}`, row]));
  table(
    $("verdicts"),
    ["#", "geographic_location", "grade", "reason"],
    verdicts.map((verdict) => {
      const row = byId.get(verdict.id);
      return [
        row ? row.line : verdict.id,
        row ? row.claim : "",
        { text: verdict.grade, className: `grade-${verdict.grade.replace(/\s+/g, "-")}` },
        verdict.reason,
      ];
    }),
    "No check run.",
  );
}

function renderHierarchy(hierarchy) {
  table(
    $("hierarchy"),
    ["latitude", "longitude", "location_resolution", "scheme", "level", "key", "value"],
    hierarchy.map((row) => [
      { text: row.latitude, className: "num" },
      { text: row.longitude, className: "num" },
      row.location_resolution ?? "",
      row.scheme,
      { text: row.level, className: "num" },
      row.key,
      row.value,
    ]),
    "No rows — this location_resolution publishes no hierarchy.",
  );
}

function popup(row, entries, verdict) {
  const parts = [`<b>${row.latitude}, ${row.longitude}</b>`];
  if (verdict) {
    parts.push(
      `<div class="grade-${verdict.grade.replace(/\s+/g, "-")}">${verdict.grade}` +
        `${verdict.reason ? `: ${verdict.reason}` : ""}</div>`,
    );
  }
  for (const entry of entries) {
    const body = entry.error
      ? `<dd>${entry.error}</dd>`
      : Object.entries(entry.result)
          .map(([field, value]) => `<dt>${field}</dt><dd>${value}</dd>`)
          .join("");
    parts.push(`<dl><dt><b>${entry.requested_type}</b></dt><dd></dd>${body}</dl>`);
  }
  return parts.join("");
}

function renderMap(rows, entries, verdicts) {
  drawn.clearLayers();

  const byCoordinate = new Map();
  for (const entry of entries) {
    const id = key(entry.latitude, entry.longitude);
    if (!byCoordinate.has(id)) byCoordinate.set(id, []);
    byCoordinate.get(id).push(entry);
  }
  const verdictByLine = new Map(
    (verdicts || []).map((verdict) => [verdict.id, verdict]),
  );

  const points = [];
  for (const row of rows) {
    if (row.error) continue;
    const forRow = byCoordinate.get(key(row.latitude, row.longitude)) || [];
    const verdict = verdictByLine.get(`row ${row.line}`);
    // Grade first when there is one: the question a check answers is the
    // one that was asked. Without it, colour by what the lookup found.
    const resolved = forRow.find((entry) => entry.result && entry.result.type !== "unknown");
    const colour = verdict
      ? GRADE_COLOURS[verdict.grade] || "#6b7580"
      : forRow.some((entry) => entry.error)
        ? TYPE_COLOURS.unknown
        : TYPE_COLOURS[resolved ? resolved.result.type : "unknown"];

    if (Math.abs(row.latitude) > 90 || Math.abs(row.longitude) > 180) continue;

    const marker = L.circleMarker([row.latitude, row.longitude], {
      radius: 6,
      color: "#fff",
      weight: 2,
      fillColor: colour,
      fillOpacity: 0.95,
    }).bindPopup(popup(row, forRow, verdict));
    drawn.addLayer(marker);
    points.push([row.latitude, row.longitude]);

    // How far the nearest polygon was, drawn to scale: a fallback names the
    // nearest unit, not the one the point is in, and the distance is the
    // whole reason to trust or distrust that name.
    const distances = forRow
      .map((entry) => entry.result && entry.result.fallback_distance_m)
      .filter((metres) => typeof metres === "number" && isFinite(metres));
    if (distances.length) {
      drawn.addLayer(
        L.circle([row.latitude, row.longitude], {
          radius: Math.max(...distances),
          color: colour,
          weight: 1,
          fillOpacity: 0.06,
        }),
      );
    }
  }

  if (points.length === 1) map.setView(points[0], Math.max(map.getZoom(), 6));
  else if (points.length) map.fitBounds(L.latLngBounds(points).pad(0.25));
}

/* ------------------------------------------------------------- submit ---- */

async function run(event) {
  event.preventDefault();
  const button = $("submit");
  button.disabled = true;
  $("error").hidden = true;

  try {
    const rows = parseRows($("coords").value, $("order").value === "lonlat");
    const claim = $("geographic_location").value.trim();
    for (const row of rows) row.claim = row.label || claim;

    const usable = rows.filter((row) => !row.error);
    if (!usable.length) {
      throw new Error(
        rows.length ? "no line held two numbers" : "no coordinates given",
      );
    }

    const coordinates = usable.map((row) => [row.latitude, row.longitude]);
    const geocoded = await post("/geocode", {
      coordinates,
      type: $("type").value,
      geographic_location: claim,
    });

    // /check grades a stated location, so run it only when a row or the form
    // names one.
    let checked = null;
    const claimed = usable.filter((row) => row.claim);
    if (claimed.length) {
      checked = await post("/check", {
        coordinates,
        samples: usable.map((row, index) => ({
          id: `row ${row.line}`,
          coord: index,
          geographic_location: row.claim,
          ...($("location_resolution").value && { location_resolution: $("location_resolution").value }),
          artificial: $("artificial").value.trim(),
        })),
      });
    }

    renderGeocode(geocoded.results, usable);
    renderVerdicts(checked ? checked.verdicts : [], usable);
    renderHierarchy(checked ? checked.location_hierarchy : []);
    renderMap(rows, geocoded.results, checked ? checked.verdicts : []);

    $("check-note").textContent = checked
      ? ""
      : "Not run: no geographic_location to check against.";
    $("rawjson").textContent = JSON.stringify({ geocode: geocoded, check: checked }, null, 2);

    const unparsed = rows.filter((row) => row.error);
    if (unparsed.length) {
      showError(
        `Skipped ${unparsed.length} line(s): ` +
          unparsed.map((row) => `${row.line} (${row.raw})`).join(", "),
      );
    }
  } catch (failure) {
    showError(failure.message);
  } finally {
    button.disabled = false;
  }
}

function showError(message) {
  const box = $("error");
  box.textContent = message;
  box.hidden = false;
}

$("form").addEventListener("submit", run);

// Draw the tables empty so their placeholders explain what each one shows.
renderGeocode([], []);
renderVerdicts([], []);
renderHierarchy([]);

loadOptions().catch((failure) => {
  $("status").textContent = `service unreachable: ${failure.message}`;
  $("status").classList.add("down");
});
