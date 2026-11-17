import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // A separate build folder lets the browser tests run their own server next to your real one.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
