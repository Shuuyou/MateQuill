import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1", "localhost", "100.120.224.40"],
  async rewrites() {
    return {
      fallback: [
        { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      ],
    };
  },
  images: { unoptimized: true },
  devIndicators: false,
  experimental: { proxyTimeout: 300_000 },
};

export default nextConfig;
