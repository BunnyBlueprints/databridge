import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { useWorkspace } from './WorkspaceContext'

export function AppShell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const { activeWorkspace } = useWorkspace()

  useEffect(() => {
    if (!mobileOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [mobileOpen])

  return (
    <div className="flex min-h-screen w-full bg-[var(--db-bg)] text-[var(--db-text-primary)]">
      {/* Desktop persistent collapsible sidebar */}
      <aside
        aria-label="Sidebar navigation"
        className={`hidden shrink-0 border-r border-zinc-200/90 bg-white transition-[width] duration-200 ease-out lg:sticky lg:top-0 lg:block lg:h-screen ${
          collapsed ? 'w-16' : 'w-60'
        }`}
      >
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((prev) => !prev)}
        />
      </aside>

      {/* Mobile slide-over sidebar */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-50 flex lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Mobile navigation"
        >
          <div
            className="fixed inset-0 bg-zinc-950/35 transition-opacity"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative z-10 h-full w-64 max-w-[82vw] border-r border-zinc-200 bg-white shadow-xl">
            <Sidebar
              collapsed={false}
              onNavigate={() => setMobileOpen(false)}
              mobileCloseButton
            />
          </aside>
        </div>
      )}

      {/* Main viewport */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top header */}
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-zinc-200/90 bg-white px-4 lg:hidden">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation menu"
              className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
            >
              <Menu className="h-4 w-4" aria-hidden="true" />
            </button>
            <span className="text-sm font-semibold tracking-tight text-zinc-900">
              DataBridge
            </span>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-zinc-50 px-2 py-0.5 text-xs font-medium text-zinc-700">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                activeWorkspace.environment === 'Production'
                  ? 'bg-emerald-500'
                  : activeWorkspace.environment === 'Staging'
                    ? 'bg-amber-500'
                    : 'bg-zinc-400'
              }`}
              aria-hidden="true"
            />
            {activeWorkspace.name}
          </span>
        </header>

        <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  )
}
