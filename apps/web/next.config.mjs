const apiOrigin = process.env.BIOADAPT_API_ORIGIN ?? "http://127.0.0.1:8000";

// The workbench holds no data of its own: every screen reads the Python API. Rewriting it to this origin
// keeps one cookie-free surface in the browser and avoids asking the API for a CORS policy it does not have.
export default {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/core-api/:path*", destination: `${apiOrigin}/:path*` }];
  },
};
