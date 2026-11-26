import type { ReactNode } from 'react'

interface TooltipProps {
  label: string
  enabled?: boolean
  children: ReactNode
}

export function Tooltip({ label, enabled = true, children }: TooltipProps) {
  if (!enabled) {
    return <>{children}</>
  }

  return (
    <div className="group/tooltip relative flex w-full items-center">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute left-full z-50 ml-2.5 rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-medium whitespace-nowrap text-white opacity-0 shadow-md transition-opacity duration-150 group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100 group-hover/tooltip:visible group-hover/tooltip:opacity-100"
      >
        {label}
      </span>
    </div>
  )
}
