/** @type {import('next').NextConfig} */
const nextConfig = {
  // El Dockerfile fija NEXT_OUTPUT=standalone para generar una imagen mínima;
  // sin la variable (npm run dev/build local, Vercel) todo sigue igual.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
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
