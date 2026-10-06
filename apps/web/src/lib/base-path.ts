/**
 * Prefix a root-relative URL with the deployment's base path (BASE_PATH in next.config.ts).
 * Next.js adds it to links and its own assets, but not to fetch() or texture URLs.
 */
export const withBasePath = (url: string) => `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${url}`;
