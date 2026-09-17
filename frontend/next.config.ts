import type { NextConfig } from "next";
const config: NextConfig = {
  poweredByHeader: false,
  experimental: { proxyClientMaxBodySize: "21mb" },
  async rewrites() {
    const origin = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:18000";
    return [
      { source: "/api/v1/:path*", destination: `${origin}/api/v1/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "object-src 'none'",
              "base-uri 'self'",
              "frame-ancestors 'none'",
              "form-action 'self'",
              `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}`,
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' blob: data:",
              `connect-src 'self'${process.env.NODE_ENV === "development" ? " ws:" : ""}`,
            ].join("; "),
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};
export default config;
