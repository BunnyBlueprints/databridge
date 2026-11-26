import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'ghost'
type ButtonSize = 'sm' | 'md'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode
  variant?: ButtonVariant
  size?: ButtonSize
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    'border border-emerald-700/10 bg-emerald-600 text-white shadow-[0_1px_2px_rgba(5,150,105,0.18)] hover:bg-emerald-700 focus-visible:ring-emerald-600',
  secondary:
    'border border-zinc-200 bg-white text-zinc-700 shadow-[0_1px_2px_rgba(15,23,42,0.03)] hover:border-zinc-300 hover:bg-zinc-50 hover:text-zinc-900 focus-visible:ring-zinc-400',
  ghost:
    'border border-transparent bg-transparent text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 focus-visible:ring-zinc-400',
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: 'h-8 px-2.5 text-xs gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
}

export function Button({
  children,
  variant = 'secondary',
  size = 'md',
  className = '',
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex cursor-pointer items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}
