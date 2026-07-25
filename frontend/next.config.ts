import type { NextConfig } from "next";

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,

  // Required by the Dockerfile's runtime stage, which copies .next/standalone.
  output: "standalone",

  async rewrites() {
    // Proxy API calls through Next so the browser only ever talks to one origin:
    // no CORS preflight, and no backend URL baked into the client bundle.
    return [{ source: "/api/backend/:path*", destination: `${BACKEND}/:path*` }];
  },

  async headers() {
    // This app renders a person's financial history. None of it should be
    // embeddable by another site, and none of it should leak via a referrer.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default config;
