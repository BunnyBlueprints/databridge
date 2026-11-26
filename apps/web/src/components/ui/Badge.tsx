import type { ReactNode } from 'react'

export type BadgeTone = 'emerald' | 'rose' | 'amber' | 'zinc' | 'slate'

interface BadgeProps {
  children: ReactNode
  tone?: BadgeTone
  dot?: boolean
  pulseDot?: boolean
  className?: string
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  emerald: 'border-emerald-200/80 bg-emerald-50 text-emerald-700',
  rose: 'border-rose-200/80 bg-rose-50 text-rose-700',
  amber: 'border-amber-200/80 bg-amber-50 text-amber-700',
  zinc: 'border-zinc-200 bg-zinc-100/80 text-zinc-700',
  slate: 'border-slate-200 bg-slate-50 text-slate-600',
}

const DOT_CLASSES: Record<BadgeTone, string> = {
  emerald: 'bg-emerald-500',
  rose: 'bg-rose-500',
  amber: 'bg-amber-500',
  zinc: 'bg-zinc-400',
  slate: 'bg-slate-400',
}

export function Badge({
  children,
  tone = 'zinc',
  dot = false,
  pulseDot = false,
  className = '',
}: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONE_CLASSES[tone]} ${className}`}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulseDot && (
            <span
              className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${DOT_CLASSES[tone]}`}
            />
          )}
          <span
            className={`relative inline-flex h-1.5 w-1.5 rounded-full ${DOT_CLASSES[tone]}`}
          />
        </span>
      )}
      {children}
    </span>
  )
}
