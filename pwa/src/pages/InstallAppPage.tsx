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
      setMessage('App instalado! Procure o ícone Santé na tela inicial.')
      return
    }
    if (result.reason === 'dismissed') {
      setMessage('Instalação cancelada. Você pode tentar de novo quando quiser.')
      return
    }
    setMessage('Neste navegador, use as instruções abaixo para adicionar à tela inicial.')
  }

  return (
    <div className="page">
      <BackHeader title="Instalar aplicativo" />
      <div className="install-app">
        <img src="/assets/icons/icon-192.png" alt="Santé Saúde" className="install-app__icon" />
        <h2>Adicionar à tela inicial</h2>
        <p>
          Instale a Santé como um app no seu celular: ícone na tela inicial, abertura em tela cheia
          (sem barra do navegador) e acesso rápido, como um aplicativo nativo.
        </p>

        <p className="install-app__hint">
          Depois de instalar, abra sempre pelo ícone na tela inicial — só assim o app fica em
          fullscreen.
        </p>

        {installed ? (
          <div className="install-app__done">
            <strong>Já instalado</strong>
            <p>Você já está usando a Santé no modo aplicativo.</p>
          </div>
        ) : (
          <>
            {canPrompt && (
              <Button loading={loading} onClick={() => void handleInstall()}>
                Adicionar à tela inicial
              </Button>
            )}

            {ios && (
              <div className="install-app__steps">
                <h3>No iPhone / Safari</h3>
                <ol>
                  <li>
                    Toque em <strong>Compartilhar</strong> (ícone de quadrado com seta).
                  </li>
                  <li>
                    Role e toque em <strong>Adicionar à Tela de Início</strong>.
                  </li>
                  <li>
                    Confirme em <strong>Adicionar</strong>.
                  </li>
                </ol>
              </div>
            )}

            {!ios && !canPrompt && (
              <div className="install-app__steps">
                <h3>No Android / Chrome</h3>
                <ol>
                  <li>Abra o menu do navegador (⋮).</li>
                  <li>
                    Toque em <strong>Instalar aplicativo</strong> ou{' '}
                    <strong>Adicionar à tela inicial</strong>.
                  </li>
                  <li>Confirme a instalação.</li>
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
