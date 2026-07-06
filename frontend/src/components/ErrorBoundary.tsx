import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Catches render errors so a single component crash doesn't produce a blank
 * page in production. Shows the error message with a retry hint.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Familiar crashed:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100dvh',
          padding: '24px',
          textAlign: 'center',
          fontFamily: 'system-ui, sans-serif',
          color: '#4a423a',
          background: '#fbf8f1',
        }}>
          <h2 style={{ margin: '0 0 12px' }}>Something went wrong</h2>
          <p style={{ margin: '0 0 8px', maxWidth: '40ch', lineHeight: 1.5 }}>
            {this.state.error.message}
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: '16px',
              padding: '10px 24px',
              border: '1px solid #9fcfc8',
              borderRadius: '9px',
              background: '#dcede9',
              color: '#2c8c83',
              fontWeight: 600,
              cursor: 'pointer',
              fontSize: '14px',
            }}
          >
            Reload
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
