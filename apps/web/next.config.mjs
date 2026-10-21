import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const apiOrigin = process.env.BIOADAPT_API_ORIGIN ?? "http://127.0.0.1:8000";

export default (phase) => ({
  reactStrictMode: true,
  // Production builds must not overwrite assets served by a live development server.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  async rewrites() {
    return [{ source: "/core-api/:path*", destination: `${apiOrigin}/:path*` }];
  },
});
