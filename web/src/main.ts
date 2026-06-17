import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "leaflet.heat";
import "./style.css";

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
}
type Feature = GeoJSON.Feature<GeoJSON.Point, WaterbodyProps>;
interface SpeciesInfo { label: string; count: number; overabundant_count: number; }

const DATA = (path: string) => `${import.meta.env.BASE_URL}data/${path}`;

// --- map setup -------------------------------------------------------------
const map = L.map("map", { center: [47.4, -120.5], zoom: 7 });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 18,
}).addTo(map);

const clusters = L.markerClusterGroup({ maxClusterRadius: 50 }).addTo(map);
const overLayer = L.layerGroup().addTo(map);
let heat: L.HeatLayer | null = null;

// --- controls --------------------------------------------------------------
const speciesSel = document.getElementById("species") as HTMLSelectElement;
const catLowland = document.getElementById("cat-lowland") as HTMLInputElement;
const catHigh = document.getElementById("cat-high") as HTMLInputElement;
const overToggle = document.getElementById("over-toggle") as HTMLInputElement;
const overWrap = document.getElementById("over-toggle-wrap") as HTMLElement;
const countEl = document.getElementById("count") as HTMLElement;

let features: Feature[] = [];

function popupHtml(p: WaterbodyProps): string {
  const facts = [
    p.county && `${p.county} County`,
    p.acres != null && `${p.acres} ac`,
    p.elevation != null && `${p.elevation} ft`,
  ].filter(Boolean).join(" · ");
  const over = p.overabundant_species.length
    ? `<p class="over">⚠ Overpopulated: ${p.overabundant_species.join(", ")}</p>` : "";
  const species = p.species.length
    ? `<ul>${p.species.map((s) => `<li>${s}</li>`).join("")}</ul>`
    : `<p class="meta">No species recorded</p>`;
  return `<div class="popup"><h3>${p.name}</h3>
    <p class="meta">${facts}</p>${over}${species}
    <p class="meta"><a href="${p.url}" target="_blank" rel="noopener">View on WDFW ↗</a></p></div>`;
}

function render(): void {
  const species = speciesSel.value; // "" = all
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
    }).bindPopup(popupHtml(p));

    if (isOver) {
      marker.addTo(overLayer);
      heatPoints.push([lat, lng, 1]);
    } else {
      clusters.addLayer(marker);
    }
  }

  if (wantOver && heatPoints.length) {
    heat = L.heatLayer(heatPoints, { radius: 35, blur: 25, minOpacity: 0.35 }).addTo(map);
  }

  countEl.textContent =
    `${visible.length} lakes shown` +
    (wantOver ? ` · ${heatPoints.length} overpopulated` : "");
}

// --- boot ------------------------------------------------------------------
async function boot(): Promise<void> {
  const [geo, speciesList] = await Promise.all([
    fetch(DATA("locations.geojson")).then((r) => r.json() as Promise<GeoJSON.FeatureCollection<GeoJSON.Point, WaterbodyProps>>),
    fetch(DATA("species.json")).then((r) => r.json() as Promise<SpeciesInfo[]>),
  ]);
  features = geo.features as Feature[];

  speciesSel.innerHTML = `<option value="">All species (${features.length} lakes)</option>`;
  for (const s of speciesList) {
    const tag = s.overabundant_count ? ` · ${s.overabundant_count} overpop.` : "";
    speciesSel.add(new Option(`${s.label} (${s.count}${tag})`, s.label));
  }

  const onChange = () => {
    overWrap.hidden = speciesSel.value === "";
    if (speciesSel.value === "") overToggle.checked = false;
    render();
  };
  [speciesSel, catLowland, catHigh, overToggle].forEach((el) =>
    el.addEventListener("change", onChange));

  render();
}

boot().catch((e) => {
  console.error(e);
  document.getElementById("count")!.textContent = "Failed to load data — run the crawler + export first.";
});
