import { useEffect, useState } from 'react'
import { fetchSubscription } from '../api/services'
import type { ApiSubscription, ApiTransaction } from '../api/types'
import { Button } from '../components/Button'

function formatMoney(v: number | string | undefined) {
  const n = typeof v === 'string' ? Number(v) : v
  if (!n || Number.isNaN(n)) return '0,00'
  return Number(n).toFixed(2).replace('.', ',')
}

function transactionLabel(t: ApiTransaction) {
  if (t.status_label) return t.status_label
  const s = String(t.status || '').toLowerCase()
  if (s.includes('paid') || s === '1' || s === 'paid') return 'Pago'
  if (s.includes('overdue')) return 'Vencido'
  return 'Pendente'
}

export function PlanPage() {
  const [subscription, setSubscription] = useState<ApiSubscription | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetchSubscription()
      .then((data) => {
        if (active) setSubscription(data)
      })
      .catch((err) => {
        if (active) setError(err?.message || 'Não há conteúdo disponível')
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
      <div className="page plan">
        <div className="empty-state">
          <div className="spinner" />
        </div>
      </div>
    )
  }

  if (error || !subscription) {
    return (
      <div className="page plan">
        <div className="empty-state">{error || 'Não há conteúdo disponível'}</div>
      </div>
    )
  }

  const value = subscription.modality?.value ?? subscription.value ?? 0
  const charges = subscription.transactions || []

  return (
    <div className="page plan">
      <h1 className="title-section" style={{ fontSize: 28, marginBottom: 20 }}>
        Meu Plano
      </h1>
      <div className="plan__card">
        <div className="plan__card-left">
          <span>Plano</span>
          <div className="plan__price">
            <span>R$</span>
            <strong>{formatMoney(value)}</strong>
          </div>
          <small style={{ marginTop: 8, color: 'var(--text)' }}>/ mensal</small>
        </div>
        <div className="plan__card-right">
          <img src="/assets/images/vector_plan.svg" alt="" />
          <strong>{subscription.modality?.name || 'Assinatura'}</strong>
          <span className="plan__status">Ativo</span>
        </div>
      </div>

      <div className="charges">
        <h3>Cobranças</h3>
        {charges.length === 0 ? (
          <div className="empty-state">Nenhuma cobrança encontrada</div>
        ) : (
          charges.map((c) => (
            <div key={c.id} className="charge-item">
              <div>
                <strong>{c.description || `Cobrança #${c.id}`}</strong>
                <span>
                  {c.due_date
                    ? `Vencimento ${new Date(c.due_date).toLocaleDateString('pt-BR')}`
                    : 'Sem vencimento'}{' '}
                  · {transactionLabel(c)}
                </span>
              </div>
              <div className="charge-item__value">
                <strong>R$ {formatMoney(c.amount ?? c.value)}</strong>
                {transactionLabel(c) === 'Pendente' && (
                  <Button style={{ marginTop: 8, padding: '8px 12px', fontSize: 12, width: 'auto' }}>
                    Pagar
                  </Button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
