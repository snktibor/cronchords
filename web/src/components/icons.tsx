import type { ReactNode } from 'react'

/**
 * 16px glyphs, the same set usageratio draws. Status is never colour alone:
 * each status icon sits next to a text label (see Status.tsx).
 */

interface IconProps {
  className?: string
}

function Glyph({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {children}
    </svg>
  )
}

export function CheckCircleIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6.2" />
      <path d="m5.4 8.2 1.8 1.8 3.5-3.8" />
    </Glyph>
  )
}

export function AlertTriangleIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M8 2.2 14.3 13H1.7L8 2.2Z" />
      <path d="M8 6.6v3" />
      <path d="M8 11.4v.1" />
    </Glyph>
  )
}

export function StopCircleIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6.2" />
      <path d="m5.9 5.9 4.2 4.2m0-4.2-4.2 4.2" />
    </Glyph>
  )
}

export function InfoCircleIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 7.4v3.6" />
      <path d="M8 5v.1" />
    </Glyph>
  )
}

export function LogOutIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M6.2 13.4H3.8c-.7 0-1.2-.5-1.2-1.2V3.8c0-.7.5-1.2 1.2-1.2h2.4" />
      <path d="M10.6 11 13.6 8l-3-3M13.6 8H6" />
    </Glyph>
  )
}
