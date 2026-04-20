'use client'

import { AuthProvider, AuthGuard } from '@/lib/auth-context'

/**
 * Client-side wrapper injected into the root layout so auth context is
 * available everywhere. The root layout itself must stay a server component
 * (it exports `metadata`), so the context provider + guard live here.
 */
export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <AuthGuard>{children}</AuthGuard>
    </AuthProvider>
  )
}
