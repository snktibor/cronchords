import type { ReactNode } from 'react'
import { AlertTriangleIcon, CheckCircleIcon, InfoCircleIcon, StopCircleIcon } from './icons'

export type Tone = 'good' | 'warning' | 'problem' | 'info'

const ICONS = {
  good: CheckCircleIcon,
  warning: AlertTriangleIcon,
  problem: StopCircleIcon,
  info: InfoCircleIcon,
}

/** A short status sentence behind its icon: the tone is never told by colour alone. */
export function Status({
  tone,
  role,
  children,
}: {
  tone: Tone
  role?: 'status' | 'alert'
  children: ReactNode
}) {
  const Icon = ICONS[tone]
  return (
    <span role={role} className={`status ${tone}`}>
      <Icon />
      <span>{children}</span>
    </span>
  )
}
