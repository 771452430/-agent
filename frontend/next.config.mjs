const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const proxyTarget = (process.env.INTERNAL_API_BASE ?? process.env.NEXT_PUBLIC_API_BASE ?? DEFAULT_API_BASE).replace(
  /\/$/,
  ""
);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: proxyTarget + "/api/:path*"
      },
      {
        source: "/health",
        destination: proxyTarget + "/health"
      }
    ];
  }
};

export default nextConfig;
