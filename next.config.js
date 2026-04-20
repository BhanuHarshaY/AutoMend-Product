/** @type {import('next').NextConfig} */

// API_PROXY_TARGET is BAKED IN at `next build` time, NOT read at runtime.
// Next.js standalone mode serializes the rewrites() output into
// routes-manifest.json during build; the runtime server uses that manifest
// and ignores subsequent env changes. Override via `--build-arg API_PROXY_TARGET`
// when building the Docker image (see infra/dockerfiles/Dockerfile.frontend).
const API_PROXY_TARGET = process.env.API_PROXY_TARGET || 'http://localhost:8000'

const nextConfig = {
  // Produce a self-contained server bundle at .next/standalone — only the
  // node_modules actually used are included. Required for the minimal
  // Dockerfile.frontend runtime image (Phase 11.1).
  output: 'standalone',

  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
