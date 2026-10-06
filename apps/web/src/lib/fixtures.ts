import events from "../../../../data/fixtures/api_events.json";
import linkRows from "../../../../data/fixtures/api_links.json";
import type { EventLink } from "./api";
// The sample links file may be empty, which TypeScript would infer as never[].
const links = linkRows as EventLink[];
export { events, links };
