import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'AutoMend — MLOps Remediation Platform',
  description: 'Zapier for MLOps: From Alert to Action in Seconds',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
