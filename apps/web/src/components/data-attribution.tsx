"use client";
import { useState } from "react";

// Every third-party source the page displays. Keep in sync with the README "Credits" section.
const GROUPS: { label: string; sources: { name: string; href: string; title: string }[] }[] = [
  { label: "Events", sources: [
    { name: "BBC News", href: "https://www.bbc.co.uk/news/world", title: "BBC News World RSS feed" },
    { name: "The Guardian", href: "https://www.theguardian.com/world", title: "The Guardian World news RSS feed" },
    { name: "Al Jazeera", href: "https://www.aljazeera.com/", title: "Al Jazeera RSS feed" },
    { name: "GDELT", href: "https://www.gdeltproject.org/", title: "The GDELT Project daily event data" },
  ] },
  { label: "Map", sources: [
    { name: "Natural Earth", href: "https://www.naturalearthdata.com/", title: "Natural Earth country shapes (public domain)" },
    { name: "© OpenStreetMap", href: "https://www.openstreetmap.org/copyright", title: "Story locations geocoded with Nominatim from OpenStreetMap data (ODbL)" },
    { name: "three-globe", href: "https://github.com/vasturiano/three-globe", title: "Elevation heightmap from the three-globe examples (MIT)" },
  ] },
  { label: "Stars", sources: [
    { name: "Yale BSC5 / CDS", href: "https://cdsarc.cds.unistra.fr/viz-bin/cat/V/50", title: "Yale Bright Star Catalogue, 5th Revised Ed. (Hoffleit & Warren 1991), CDS Strasbourg" },
  ] },
];

/** Small corner credit line, as on most web maps. Collapses to a toggle on narrow screens. */
export default function DataAttribution() {
  const [open, setOpen] = useState(false);
  return <footer className="data-attribution" data-open={open || undefined}>
    <button type="button" className="data-attribution-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>Data sources</button>
    <p>{GROUPS.map(group => <span key={group.label} className="data-attribution-group">
      {group.label}: {group.sources.map((source, index) => <span key={source.name}>
        {index > 0 && " · "}<a href={source.href} title={source.title} target="_blank" rel="noreferrer">{source.name}</a>
      </span>)}
    </span>)}</p>
  </footer>;
}
