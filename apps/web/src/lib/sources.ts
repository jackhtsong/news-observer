/** Display names for mart.event.source values (jobs/news/sources.py and the GDELT loader). */
const NAMES: Record<string, string> = {
  bbc: "BBC", guardian: "The Guardian", aljazeera: "Al Jazeera", conflict_csv: "GDELT",
};
// Sources that collect articles from many outlets: the card names the outlet too.
const AGGREGATORS = new Set(["conflict_csv"]);

const outlet = (url: string | null | undefined) => {
  try {
    const parsed = new URL(url ?? "");
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.hostname.replace(/^www\./, "") : null;
  } catch { return null; }
};

/**
 * Upper-case source label for cards, as the card style shows it. GDELT stories name the
 * outlet that published the article ("NEWS.WJCT.ORG VIA GDELT"); unknown sources read as-is.
 */
export function sourceLabel(source: string, url?: string | null) {
  const name = NAMES[source] ?? source;
  const host = AGGREGATORS.has(source) ? outlet(url) : null;
  return (host ? `${host} via ${name}` : name).toUpperCase();
}
