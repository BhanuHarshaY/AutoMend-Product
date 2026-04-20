'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { LogIn, AlertCircle } from 'lucide-react'

import { useAuth } from '@/lib/auth-context'
import { ApiError } from '@/lib/api'

export default function LoginPage() {
  const { login, isAuthenticated, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // If the user is already logged in (e.g., revisits /login), bounce to /
  useEffect(() => {
    if (!authLoading && isAuthenticated) {
      router.replace('/')
    }
  }, [authLoading, isAuthenticated, router])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      // login() redirects to / on success
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 401 ? 'Invalid email or password' : err.detail)
      } else {
        setError('Unable to reach server. Is the backend running?')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0e1a] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-semibold text-[#F4F5F8] mb-2">
            Auto<span className="text-[#E8935A]">Mend</span>
          </h1>
          <p className="text-sm text-[#6B7588]">MLOps incident remediation</p>
        </div>

        {/* Card */}
        <div className="bg-[#1A1F2E] border border-[#2A3248] rounded-xl p-6 shadow-xl">
          <h2 className="text-lg font-semibold text-[#F4F5F8] mb-6">Sign in</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-xs font-medium text-[#c0cce0] mb-1.5">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                autoFocus
                className="w-full bg-[#0D0F1A] border border-[#2A3248] rounded-lg px-3 py-2.5 text-sm text-[#F4F5F8] placeholder-[#3a4a6b] focus:outline-none focus:border-[#E8935A] transition-colors"
                placeholder="you@example.com"
                disabled={submitting}
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-xs font-medium text-[#c0cce0] mb-1.5">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full bg-[#0D0F1A] border border-[#2A3248] rounded-lg px-3 py-2.5 text-sm text-[#F4F5F8] placeholder-[#3a4a6b] focus:outline-none focus:border-[#E8935A] transition-colors"
                placeholder="••••••••"
                disabled={submitting}
              />
            </div>

            {error && (
              <div className="flex items-start gap-2 text-xs text-[#e63946] bg-[#e63946]/10 border border-[#e63946]/30 rounded-lg px-3 py-2">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !email || !password}
              className="w-full bg-gradient-to-r from-[#E8935A] to-[#6B7FE8] text-white font-medium text-sm py-2.5 rounded-lg hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity flex items-center justify-center gap-2"
            >
              {submitting ? (
                <span>Signing in…</span>
              ) : (
                <>
                  <LogIn className="w-4 h-4" />
                  <span>Sign in</span>
                </>
              )}
            </button>
          </form>

          <p className="text-xs text-[#6B7588] mt-6 pt-4 border-t border-[#2A3248]">
            New accounts are created by an administrator.
          </p>
        </div>
      </div>
    </div>
  )
}
