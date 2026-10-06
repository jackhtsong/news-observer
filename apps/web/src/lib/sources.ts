/** Display names for mart.event.source values (jobs/news/sources.py and the GDELT loader). */
const NAMES: Record<string, string> = {
  bbc: "BBC", guardian: "The Guardian", aljazeera: "Al Jazeera", conflict_csv: "GDELT",
};

/** Upper-case source label for cards, as the card style shows it; unknown sources read as-is. */
export const sourceLabel = (source: string) => (NAMES[source] ?? source).toUpperCase();
