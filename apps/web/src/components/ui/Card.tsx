import type { HTMLAttributes, ReactNode } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
}

export function Card({ children, className = '', ...props }: CardProps) {
  return (
    <div
      className={`rounded-lg border border-zinc-200/90 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}
      {...props}
    >
      {children}
    </div>
  )
}

interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
}

export function CardHeader({
  children,
  className = '',
  ...props
}: CardHeaderProps) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-5 py-3.5 ${className}`}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardTitle({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <h2
      className={`text-sm font-semibold tracking-tight text-zinc-900 ${className}`}
    >
      {children}
    </h2>
  )
}

export function CardDescription({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <p className={`text-xs text-zinc-500 ${className}`}>{children}</p>
}

export function CardContent({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={`p-5 ${className}`}>{children}</div>
}
