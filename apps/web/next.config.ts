import type { NextConfig } from "next";
import path from "node:path";

// Serve the app under a path prefix, e.g. BASE_PATH=/globe when a portfolio site forwards
// yoursite.com/globe to this deployment. Empty (the default) serves it at the root.
const basePath = (process.env.BASE_PATH ?? "").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  basePath,
  // Client code prefixes its own fetches and asset URLs with this (src/lib/base-path.ts).
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  turbopack: { root: path.resolve(__dirname, "../..") },
  outputFileTracingRoot: path.resolve(__dirname, "../.."),
};

export default nextConfig;
