import {
  Braces,
  Database,
  FileSpreadsheet,
  Globe,
  Layers,
  Server,
} from 'lucide-react'
import type { ConnectorType } from '../../types/dashboard'

interface ConnectorIconProps {
  type: ConnectorType
  size?: 'sm' | 'md'
}

const CONNECTOR_STYLE: Record<
  ConnectorType,
  { bg: string; fg: string; border: string }
> = {
  PostgreSQL: {
    bg: 'bg-slate-100',
    fg: 'text-slate-700',
    border: 'border-slate-200/80',
  },
  MySQL: {
    bg: 'bg-zinc-100',
    fg: 'text-zinc-700',
    border: 'border-zinc-200/80',
  },
  MongoDB: {
    bg: 'bg-emerald-50',
    fg: 'text-emerald-700',
    border: 'border-emerald-200/70',
  },
  'REST API': {
    bg: 'bg-zinc-100',
    fg: 'text-zinc-700',
    border: 'border-zinc-200/80',
  },
  CSV: {
    bg: 'bg-stone-100',
    fg: 'text-stone-700',
    border: 'border-stone-200/80',
  },
  JSON: {
    bg: 'bg-neutral-100',
    fg: 'text-neutral-700',
    border: 'border-neutral-200/80',
  },
}

export function ConnectorIcon({ type, size = 'md' }: ConnectorIconProps) {
  const style = CONNECTOR_STYLE[type]
  const boxSize = size === 'sm' ? 'h-6 w-6 rounded' : 'h-8 w-8 rounded-md'
  const iconSize = size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4'

  const renderGlyph = () => {
    switch (type) {
      case 'PostgreSQL':
        return <Database className={iconSize} aria-hidden="true" />
      case 'MySQL':
        return <Server className={iconSize} aria-hidden="true" />
      case 'MongoDB':
        return <Layers className={iconSize} aria-hidden="true" />
      case 'REST API':
        return <Globe className={iconSize} aria-hidden="true" />
      case 'CSV':
        return <FileSpreadsheet className={iconSize} aria-hidden="true" />
      case 'JSON':
        return <Braces className={iconSize} aria-hidden="true" />
    }
  }

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center border ${boxSize} ${style.bg} ${style.fg} ${style.border}`}
      title={type}
    >
      {renderGlyph()}
    </span>
  )
}
