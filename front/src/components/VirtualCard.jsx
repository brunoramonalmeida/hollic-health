import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { formatDate } from '../dates'
import { SUBSCRIPTION_STATUS, statusLabel } from '../labels'

/**
 * The patient's virtual card: name, number, plan and the 6-digit code that
 * changes every 30 seconds. The code is computed by the server; the card is
 * fetched again whenever the current code expires (and on demand).
 */
export default function VirtualCard({ path, onError }) {
  const [card, setCard] = useState(null)
  const [remaining, setRemaining] = useState(0)
  const [loading, setLoading] = useState(false)
  const timer = useRef(null)

  async function load() {
    setLoading(true)
    try {
      const data = await api(path, { silent: true })
      setCard(data)
      setRemaining(data.seconds_remaining)
    } catch (err) {
      onError?.(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setCard(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  // Count down locally; fetch the next code when this one expires.
  useEffect(() => {
    if (!card) return undefined
    timer.current = setInterval(() => {
      setRemaining((current) => {
        if (current <= 1) {
          load()
          return card.period_seconds
        }
        return current - 1
      })
    }, 1000)
    return () => clearInterval(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card])

  if (!card) return <p className="muted">Loading the card…</p>

  const percent = Math.round((remaining / card.period_seconds) * 100)
  const code = `${card.code.slice(0, 3)} ${card.code.slice(3)}`
  return (
    <div className={`virtual-card ${loading ? 'loading' : ''}`} data-testid="virtual-card">
      <div className="virtual-card-head">
        <span className="brand-mark">S</span>
        <div>
          <strong>Santé Saúde</strong>
          <small>Virtual card</small>
        </div>
        <span className="card-number">{card.card_number}</span>
      </div>
      <div className="virtual-card-body">
        <div>
          <small>Patient</small>
          <strong data-testid="card-patient">{card.patient_name}</strong>
          {card.dependent_id && <small>dependent of {card.holder_name}</small>}
        </div>
        <div>
          <small>Plan</small>
          <strong>{card.plan_name || '—'}</strong>
          {card.plan_status && (
            <small>
              <span className={`badge ${card.plan_status}`}>{statusLabel(SUBSCRIPTION_STATUS, card.plan_status)}</span>
              {card.valid_until ? ` · valid until ${formatDate(card.valid_until)}` : ''}
              {card.appointment_limit > 0 && card.appointments_remaining != null && (
                <> · {card.appointments_remaining} of {card.appointment_limit} appointment{card.appointment_limit === 1 ? '' : 's'} left this period</>
              )}
            </small>
          )}
        </div>
      </div>
      <div className="virtual-card-code">
        <small>Show this code to start your appointment</small>
        <strong data-testid="card-code" aria-live="polite">{code}</strong>
        <div className="countdown" role="progressbar" aria-valuenow={remaining} aria-valuemin={0} aria-valuemax={card.period_seconds}>
          <i style={{ width: `${percent}%` }} />
        </div>
        <small data-testid="card-remaining">New code in {remaining}s</small>
      </div>
    </div>
  )
}
