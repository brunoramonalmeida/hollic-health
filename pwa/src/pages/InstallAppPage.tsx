import { useState } from 'react'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { usePwaInstall } from '../hooks/usePwaInstall'

export function InstallAppPage() {
  const { installed, ios, canPrompt, install } = usePwaInstall()
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleInstall() {
    setMessage('')
    setLoading(true)
    const result = await install()
    setLoading(false)
    if (result.ok) {
      setMessage('App installed. Look for the Hollic icon on your home screen.')
      return
    }
    if (result.reason === 'dismissed') {
      setMessage('Installation cancelled. You can try again whenever you like.')
      return
    }
    setMessage('In this browser, use the steps below to add it to your home screen.')
  }

  return (
    <div className="page">
      <BackHeader title="Install the app" />
      <div className="install-app">
        <p className="install-app__name">Hollic Health</p>
        <h2>Add to home screen</h2>
        <p>
          Install Hollic as an app on your phone: a home-screen icon, full-screen launch
          (no browser bar) and quick access, like a native app.
        </p>

        <p className="install-app__hint">
          After installing, always open it from the home-screen icon — that is what keeps the app
          full screen.
        </p>

        {installed ? (
          <div className="install-app__done">
            <strong>Already installed</strong>
            <p>You are already using Hollic as an app.</p>
          </div>
        ) : (
          <>
            {canPrompt && (
              <Button loading={loading} onClick={() => void handleInstall()}>
                Add to home screen
              </Button>
            )}

            {ios && (
              <div className="install-app__steps">
                <h3>On iPhone / Safari</h3>
                <ol>
                  <li>
                    Tap <strong>Share</strong> (the square with an arrow).
                  </li>
                  <li>
                    Scroll and tap <strong>Add to Home Screen</strong>.
                  </li>
                  <li>
                    Confirm with <strong>Add</strong>.
                  </li>
                </ol>
              </div>
            )}

            {!ios && !canPrompt && (
              <div className="install-app__steps">
                <h3>On Android / Chrome</h3>
                <ol>
                  <li>Open the browser menu (⋮).</li>
                  <li>
                    Tap <strong>Install app</strong> or <strong>Add to Home screen</strong>.
                  </li>
                  <li>Confirm the installation.</li>
                </ol>
              </div>
            )}
          </>
        )}

        {message && <p className="install-app__message">{message}</p>}
      </div>
    </div>
  )
}
