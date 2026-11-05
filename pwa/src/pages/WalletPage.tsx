import { useEffect, useState } from 'react'
import { fetchSanteCard } from '../api/services'
import type { ApiSanteCard } from '../api/types'

export function WalletPage() {
  const [wallet, setWallet] = useState<ApiSanteCard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetchSanteCard()
      .then((data) => {
        if (active) setWallet(data)
      })
      .catch((err) => {
        if (active) setError(err?.message || 'Carteira digital indisponível no momento')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  if (loading) {
    return (
      <div className="page">
        <div className="empty-state">
          <div className="spinner" />
        </div>
      </div>
    )
  }

  if (error || !wallet) {
    return (
      <div className="page">
        <div className="empty-state">{error || 'Carteira digital indisponível no momento'}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="wallet">
        <h2 className="title-page" style={{ marginBottom: 12 }}>
          Carteira Digital
        </h2>
        <div
          className="wallet__hero"
          style={{ backgroundImage: "url('/assets/images/wallet.jpg')" }}
        >
          <div className="wallet__hero-content">
            <strong>{wallet.modality}</strong>
            <p>{wallet.description}</p>
          </div>
        </div>
        <div className="wallet__body">
          <p className="wallet__code">{wallet.user_code}</p>
          <div className="wallet__grid">
            <div>
              <span>Titular</span>
              <strong>{wallet.holder}</strong>
            </div>
            <div>
              <span>Adesão</span>
              <strong>
                {wallet.accession_date
                  ? new Date(wallet.accession_date).toLocaleDateString('pt-BR')
                  : '—'}
              </strong>
            </div>
            <div>
              <span>Fim da carência</span>
              <strong>
                {wallet.grace_period_ends_at
                  ? new Date(wallet.grace_period_ends_at).toLocaleDateString('pt-BR')
                  : '—'}
              </strong>
            </div>
          </div>

          <h3>Dependentes</h3>
          <ul className="wallet__list">
            {(wallet.dependents || []).length === 0 ? (
              <li>Nenhum dependente</li>
            ) : (
              wallet.dependents.map((name) => <li key={name}>{name}</li>)
            )}
          </ul>

          <h3>Serviços incluídos</h3>
          <ul className="wallet__list">
            {(wallet.services || []).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
