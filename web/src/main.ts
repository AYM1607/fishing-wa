import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "leaflet.heat";
import "./style.css";

interface Stock { date: string; species: string; number: number | null; }
interface WaterbodyProps {
  id: string;
  name: string;
  category: "lowland" | "high";
  county: string | null;
  acres: number | null;
  elevation: number | null;
  url: string;
  species: string[];
  overabundant_species: string[];
  last_stocked: string | null;
  recent_stocks: Stock[];
}
type Feature = GeoJSON.Feature<GeoJSON.Point, WaterbodyProps>;
interface SpeciesInfo { label: string; count: number; overabundant_count: number; }
interface StockEvent {
  id: string; name: string; county: string | null; lat: number; lng: number;
  date: string; species: string; number: number | null;
}

const DATA = (path: string) => `${import.meta.env.BASE_URL}data/${path}`;
const fmt = (n: number | null) => (n == null ? "?" : n.toLocaleString());

// Recency buckets shared by markers and the legend.
const DAY = 86_400_000;
function daysSince(date: string): number {
  return Math.floor((Date.now() - new Date(date + "T00:00:00").getTime()) / DAY);
}
function recencyColor(date: string | null): string {
  if (!date) return "#9ca3af";
  const d = daysSince(date);
  if (d <= 7) return "#15803d";
  if (d <= 30) return "#22c55e";
  if (d <= 90) return "#84cc16";
  return "#9ca3af";
}

// --- map setup -------------------------------------------------------------
const map = L.map("map", { center: [47.4, -120.5], zoom: 7 });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 18,
}).addTo(map);

const clusters = L.markerClusterGroup({ maxClusterRadius: 50 });
const overLayer = L.layerGroup();
const stockLayer = L.layerGroup();
let heat: L.HeatLayer | null = null;

// --- state + data ----------------------------------------------------------
let features: Feature[] = [];
const featureById = new Map<string, Feature>();
let stockEvents: StockEvent[] = [];

// --- controls --------------------------------------------------------------
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const speciesSel = $<HTMLSelectElement>("species");
const catLowland = $<HTMLInputElement>("cat-lowland");
const catHigh = $<HTMLInputElement>("cat-high");
const overToggle = $<HTMLInputElement>("over-toggle");
const overWrap = $<HTMLElement>("over-toggle-wrap");
const countEl = $<HTMLElement>("count");
const searchInput = $<HTMLInputElement>("search");
const searchResults = $<HTMLUListElement>("search-results");
const stockList = $<HTMLUListElement>("stock-list");

function popupHtml(p: WaterbodyProps): string {
  const facts = [
    p.county && `${p.county} County`,
    p.acres != null && `${p.acres} ac`,
    p.elevation != null && `${p.elevation} ft`,
  ].filter(Boolean).join(" · ");
  const over = p.overabundant_species.length
    ? `<p class="over">⚠ Overpopulated: ${p.overabundant_species.join(", ")}</p>` : "";
  const stock = p.last_stocked
    ? `<p class="stocked">🐟 Last stocked ${p.last_stocked}</p>
       <ul class="stocks">${p.recent_stocks.slice(0, 5)
        .map((s) => `<li>${s.date} — ${s.species} (${fmt(s.number)})</li>`).join("")}</ul>`
    : "";
  const species = p.species.length
    ? `<details><summary>${p.species.length} species</summary><ul>${
        p.species.map((s) => `<li>${s}</li>`).join("")}</ul></details>`
    : "";
  return `<div class="popup"><h3>${p.name}</h3>
    <p class="meta">${facts}</p>${over}${stock}${species}
    <p class="meta"><a href="${p.url}" target="_blank" rel="noopener">View on WDFW ↗</a></p></div>`;
}

function flyToLake(id: string): void {
  const f = featureById.get(id);
  if (!f) return;
  const [lng, lat] = f.geometry.coordinates;
  map.flyTo([lat, lng], 13);
  L.popup({ autoPan: true }).setLatLng([lat, lng]).setContent(popupHtml(f.properties)).openOn(map);
}

// --- explore view ----------------------------------------------------------
function renderExplore(): void {
  const species = speciesSel.value;
  const wantOver = species !== "" && overToggle.checked;
  const cats = new Set<string>();
  if (catLowland.checked) cats.add("lowland");
  if (catHigh.checked) cats.add("high");

  clusters.clearLayers();
  overLayer.clearLayers();
  if (heat) { map.removeLayer(heat); heat = null; }

  const visible = features.filter((f) => {
    const p = f.properties;
    if (!cats.has(p.category)) return false;
    if (species && !p.species.includes(species)) return false;
    return true;
  });

  const heatPoints: [number, number, number][] = [];
  for (const f of visible) {
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    const isOver = wantOver && p.overabundant_species.includes(species);
    const marker = L.circleMarker([lat, lng], {
      radius: isOver ? 7 : 5,
      color: isOver ? "#ef4444" : "#0ea5e9",
      weight: 1,
      fillColor: isOver ? "#ef4444" : "#38bdf8",
      fillOpacity: 0.8,
    }).bindPopup(() => popupHtml(p));
    if (isOver) { marker.addTo(overLayer); heatPoints.push([lat, lng, 1]); }
    else clusters.addLayer(marker);
  }
  if (wantOver && heatPoints.length) {
    heat = L.heatLayer(heatPoints, { radius: 35, blur: 25, minOpacity: 0.35 }).addTo(map);
  }
  countEl.textContent =
    `${visible.length} lakes shown` + (wantOver ? ` · ${heatPoints.length} overpopulated` : "");
}

// --- recent stocks view ----------------------------------------------------
function renderStocks(): void {
  stockLayer.clearLayers();
  const stocked = features.filter((f) => f.properties.last_stocked);
  for (const f of stocked) {
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    const days = daysSince(p.last_stocked!);
    L.circleMarker([lat, lng], {
      radius: days <= 7 ? 8 : days <= 30 ? 7 : 6,
      color: "#1e293b", weight: 1,
      fillColor: recencyColor(p.last_stocked), fillOpacity: 0.9,
    }).bindPopup(() => popupHtml(p)).addTo(stockLayer);
  }
  countEl.textContent = `${stocked.length} lakes stocked · ${stockEvents.length} events`;
}

const LIST_CAP = 400;
function buildStockList(): void {
  const rows = stockEvents.slice(0, LIST_CAP).map((e) => {
    const color = recencyColor(e.date);
    return `<li data-id="${e.id}">
      <span class="rdot" style="background:${color}"></span>
      <span class="when">${e.date}</span>
      <span class="what"><strong>${e.name}</strong>${e.county ? ` · ${e.county}` : ""}<br>
        ${e.species} · ${fmt(e.number)}</span></li>`;
  }).join("");
  const more = stockEvents.length > LIST_CAP
    ? `<li class="more">+${stockEvents.length - LIST_CAP} more events…</li>` : "";
  stockList.innerHTML = rows + more;
}
stockList.addEventListener("click", (e) => {
  const li = (e.target as HTMLElement).closest("li[data-id]") as HTMLElement | null;
  if (li) flyToLake(li.dataset.id!);
});

// --- mode switching --------------------------------------------------------
function setMode(next: "explore" | "stocks"): void {
  document.querySelectorAll<HTMLElement>(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === next));
  $("panel-explore").hidden = next !== "explore";
  $("panel-stocks").hidden = next !== "stocks";

  [clusters, overLayer, stockLayer].forEach((l) => map.removeLayer(l));
  if (heat) { map.removeLayer(heat); heat = null; }
  if (next === "explore") {
    clusters.addTo(map); overLayer.addTo(map); renderExplore();
  } else {
    stockLayer.addTo(map); renderStocks();
  }
}
document.querySelector(".tabs")!.addEventListener("click", (e) => {
  const tab = (e.target as HTMLElement).closest(".tab") as HTMLElement | null;
  if (tab) setMode(tab.dataset.tab as "explore" | "stocks");
});

// --- search ----------------------------------------------------------------
function runSearch(): void {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) { searchResults.hidden = true; searchResults.innerHTML = ""; return; }
  const hits = features
    .filter((f) => f.properties.name.toLowerCase().includes(q))
    .slice(0, 8);
  searchResults.innerHTML = hits.map((f) => {
    const p = f.properties;
    return `<li data-id="${p.id}">${p.name}<span>${p.county ?? ""}</span></li>`;
  }).join("");
  searchResults.hidden = hits.length === 0;
}
searchInput.addEventListener("input", runSearch);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = searchResults.querySelector("li[data-id]") as HTMLElement | null;
    if (first) { flyToLake(first.dataset.id!); searchResults.hidden = true; }
  } else if (e.key === "Escape") { searchResults.hidden = true; }
});
searchResults.addEventListener("mousedown", (e) => {
  const li = (e.target as HTMLElement).closest("li[data-id]") as HTMLElement | null;
  if (li) { flyToLake(li.dataset.id!); searchInput.value = ""; searchResults.hidden = true; }
});
searchInput.addEventListener("blur", () => setTimeout(() => (searchResults.hidden = true), 150));

// --- boot ------------------------------------------------------------------
async function boot(): Promise<void> {
  const [geo, speciesList, stocks] = await Promise.all([
    fetch(DATA("locations.geojson")).then((r) => r.json() as Promise<GeoJSON.FeatureCollection<GeoJSON.Point, WaterbodyProps>>),
    fetch(DATA("species.json")).then((r) => r.json() as Promise<SpeciesInfo[]>),
    fetch(DATA("stocks.json")).then((r) => r.json() as Promise<StockEvent[]>),
  ]);
  features = geo.features as Feature[];
  features.forEach((f) => featureById.set(f.properties.id, f));
  stockEvents = stocks;

  speciesSel.innerHTML = `<option value="">All species (${features.length} lakes)</option>`;
  for (const s of speciesList) {
    const tag = s.overabundant_count ? ` · ${s.overabundant_count} overpop.` : "";
    speciesSel.add(new Option(`${s.label} (${s.count}${tag})`, s.label));
  }

  const onExploreChange = () => {
    overWrap.hidden = speciesSel.value === "";
    if (speciesSel.value === "") overToggle.checked = false;
    renderExplore();
  };
  [speciesSel, catLowland, catHigh, overToggle].forEach((el) =>
    el.addEventListener("change", onExploreChange));

  buildStockList();
  clusters.addTo(map); overLayer.addTo(map);
  renderExplore();
}

boot().catch((e) => {
  console.error(e);
  countEl.textContent = "Failed to load data — run the crawler + export first.";
});
