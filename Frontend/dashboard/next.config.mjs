/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api-backend/:path*",
        destination: "http://52.88.48.137/:path*",
      },
    ];
  },
};

export default nextConfig;
