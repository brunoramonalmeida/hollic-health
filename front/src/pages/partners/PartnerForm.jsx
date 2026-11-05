import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { STATES } from '../../br'
import { formatDate } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, headcountLabel, payerLabel, statusLabel } from '../../labels'
import { useSession } from '../../session'

const empty = { name: '', cnpj: '', contact_name: '', email: '', phone: '', city: '', state: '', notes: '', active: true }

export default function PartnerForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { canManagePartners } = useSession()
  const readOnly = !canManagePartners
  const [form, setForm] = useState(empty)
  const [agreements, setAgreements] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    if (!id) {
      setForm(empty)
      setAgreements([])
      return undefined
    }
    // A superseded load (another id, React's dev double mount) must not
    // overwrite what the user already typed.
    let active = true
    Promise.all([api(`/partners/${id}`), api(`/agreements?partner_id=${id}`)])
      .then(([row, terms]) => {
        if (!active) return
        setForm({
          name: row.name,
          cnpj: row.cnpj,
          contact_name: row.contact_name,
          email: row.email,
          phone: row.phone,
          city: row.city,
          state: row.state,
          notes: row.notes,
          active: row.active,
        })
        setAgreements(terms)
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const saved = await api(id ? `/partners/${id}` : '/partners', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Partner updated' : 'Partner created — now negotiate its agreement',
        body: form,
      })
      // A new partner goes on to its first agreement.
      navigate(id ? '/partners' : `/partners/${saved.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/partners', label: 'Partner companies' }}
        title={id ? form.name || 'Partner' : 'New partner company'}
        hint="The company the plans are negotiated with. Its contact email and its portal logins (Users → role Partner company) receive the agreement's notices and the receipts of its invoices."
      >
        {id && <Link className="button ghost" to={`/partners/${id}/statement`}>Statement</Link>}
      </PageHeader>
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <fieldset disabled={readOnly}>
            <legend>Company</legend>
            <label>
              Name *
              <input value={form.name} required maxLength={160} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              CNPJ
              <input value={form.cnpj} maxLength={18} placeholder="00.000.000/0000-00" onChange={(event) => set('cnpj', event.target.value)} />
            </label>
            <label>
              Contact (HR / benefits)
              <input value={form.contact_name} maxLength={120} onChange={(event) => set('contact_name', event.target.value)} />
            </label>
            <label>
              Contact email
              <input type="email" value={form.email} maxLength={180} onChange={(event) => set('email', event.target.value)} />
            </label>
            <label>
              Phone
              <input value={form.phone} maxLength={30} onChange={(event) => set('phone', event.target.value)} />
            </label>
            <div className="fields narrow">
              <label>
                City
                <input value={form.city} maxLength={80} onChange={(event) => set('city', event.target.value)} />
              </label>
              <label>
                State
                <select value={form.state} onChange={(event) => set('state', event.target.value)}>
                  <option value="">—</option>
                  {STATES.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
                </select>
              </label>
            </div>
            <label className="check">
              <input type="checkbox" checked={form.active} onChange={(event) => set('active', event.target.checked)} />
              Active (an inactive partner takes no new enrollments and loses its portal access; its employees keep their plans)
            </label>
            <label>
              Notes
              <textarea value={form.notes} rows={2} onChange={(event) => set('notes', event.target.value)} />
            </label>
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
            <Link className="button ghost" to="/partners">{readOnly ? 'Back' : 'Cancel'}</Link>
          </div>
        </form>
        <div className="stack">
          {!id && <p className="muted">Create the company, then negotiate its six-month agreement: the plans, the conditions and the minimum of employees.</p>}
          {id && (
            <div className="card">
              <div className="card-head">
                <h2>Agreements</h2>
                {canManagePartners && <Link className="button ghost" to={`/agreements/new?partner=${id}`}>New agreement</Link>}
              </div>
              <table>
                <thead>
                  <tr><th>Agreement</th><th>Contract</th><th>Payer</th><th>This month</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {agreements.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <Link to={`/agreements/${row.id}`}>{row.name}</Link>
                        <br />
                        <small className="muted">{row.plans.map((plan) => `${plan.modality_name}${plan.discount_percent ? ` ${plan.discount_percent}% off` : ''}`).join(' · ')}</small>
                      </td>
                      <td>{formatDate(row.starts_on)} – {formatDate(row.ends_on)}</td>
                      <td>{payerLabel(row.payer, row.company_share_percent)}</td>
                      <td>
                        <span className={`badge ${row.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(row.employees, row.min_employees)}</span>
                      </td>
                      <td><span className={`badge ${AGREEMENT_BADGE[row.status]}`}>{statusLabel(AGREEMENT_STATUS, row.status)}</span></td>
                    </tr>
                  ))}
                  {agreements.length === 0 && <tr><td colSpan={5} className="muted">No agreement yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
