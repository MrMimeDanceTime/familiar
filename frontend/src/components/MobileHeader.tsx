import type { ReactNode } from 'react'

interface MobileHeaderProps {
  /** When true, shows a back arrow that calls onBack. */
  showBack?: boolean
  onBack?: () => void
  title?: string
  children?: ReactNode
}

export function MobileHeader({ showBack, onBack, title, children }: MobileHeaderProps) {
  return (
    <header className="mobile-header">
      <div className="mobile-header__left">
        {showBack && (
          <button
            className="mobile-header__back"
            onClick={onBack}
            aria-label="Back"
          >
            ←
          </button>
        )}
        {title && <span className="mobile-header__title">{title}</span>}
      </div>
      {children && <div className="mobile-header__actions">{children}</div>}
    </header>
  )
}
