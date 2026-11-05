import { useEffect, useState } from 'react'
import { api } from '../api'
import PageHeader from '../components/PageHeader'
import VirtualCard from '../components/VirtualCard'
import { useSession } from '../session'

/**
 * The client's virtual cards: the holder's and one per dependent. The code
 * on the card is what the clinic asks for to start the appointment, so only
 * the patient's own login can see it.
 */
export default function MyCard() {
  const session = useSession()
  const [dependents, setDependents] = useState([])
  const [selected, setSelected] = useState('') // '' = the holder
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)

  useEffect(() => {
    api(`/customers/${session.customerId}/dependents`).then(setDependents).catch((err) => setError(err.message))
  }, [session.customerId])

  const base = `/customers/${session.customerId}`
  const path = selected ? `${base}/dependents/${selected}/card` : `${base}/card`

  async function reset() {
    if (!window.confirm('Issue a new card? The codes of the current one stop working immediately.')) return
    try {
      await api(`${path}/reset`, { method: 'POST', message: 'New card issued' })
      setVersion((current) => current + 1)
    } catch {
      /* toast already shown */
    }
  }

  return (
    <section>
      <PageHeader
        crumb="My area / Virtual card"
        title="My virtual card"
        hint="At the clinic, show the code on the card: the reception or the professional types it to start your appointment. The code changes every 30 seconds and only you can see it."
      >
        {dependents.length > 0 && (
          <label className="inline">
            Card of
            <select aria-label="Card of" value={selected} onChange={(event) => setSelected(event.target.value)}>
              <option value="">{session.user.customer_name || 'Me (holder)'}</option>
              {dependents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
        )}
        <button type="button" className="ghost" onClick={reset}>Issue a new card</button>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card-stage">
        <VirtualCard key={`${path}-${version}`} path={path} onError={setError} />
        <p className="muted">
          If you believe someone else knows your codes, issue a new card: the old codes stop working at once. You are notified whenever a code of yours is used to start an appointment.
        </p>
      </div>
    </section>
  )
}
