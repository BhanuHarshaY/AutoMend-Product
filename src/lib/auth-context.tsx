'use client'

/**
 * React authentication context.
 *
 * Wraps the app and exposes { user, login, logout, isAuthenticated, isLoading }.
 * On mount, if a token is present in localStorage, calls api.auth.me() to
 * validate it and load the user. Expired/invalid tokens are cleared silently.
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'

import { api, clearTokens, getAccessToken, UserResponse } from './api'

interface AuthContextValue {
  user: UserResponse | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const router = useRouter()

  // On mount: if a token exists, try to load the user. Silently clear bad tokens.
  useEffect(() => {
    const token = getAccessToken()
    if (!token) {
      setIsLoading(false)
      return
    }
    let cancelled = false
    api.auth.me()
      .then((u) => { if (!cancelled) setUser(u) })
      .catch(() => { if (!cancelled) clearTokens() })
      .finally(() => { if (!cancelled) setIsLoading(false) })
    return () => { cancelled = true }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    await api.auth.login(email, password)
    const u = await api.auth.me()
    setUser(u)
    router.push('/')
  }, [router])

  const logout = useCallback(() => {
    clearTokens()
    setUser(null)
    router.push('/login')
  }, [router])

  return (
    <AuthContext.Provider value={{
      user,
      isAuthenticated: user !== null,
      isLoading,
      login,
      logout,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}

/**
 * AuthGuard — redirects to /login if the user is not authenticated.
 * Skips the redirect when the current path is /login itself (so the login
 * page can render without an infinite loop).
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (isLoading) return
    if (!isAuthenticated && pathname !== '/login') {
      router.replace('/login')
    }
  }, [isAuthenticated, isLoading, pathname, router])

  // While loading or redirecting away, show a spinner (prevents flash of
  // authenticated UI before the token check completes).
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#0a0e1a]">
        <div className="text-[#c0cce0] text-sm">Loading…</div>
      </div>
    )
  }

  if (!isAuthenticated && pathname !== '/login') {
    // Redirect is in flight; render nothing to avoid a flash of the protected page
    return null
  }

  return <>{children}</>
}
