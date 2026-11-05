import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate, isoDate, today } from '../../dates'
import { graceLabel, headcountLabel, payerLabel } from '../../labels'

const empty = { agreement_id: '', modality_id: '', joined_on: '', company_share_percent: '', name: '', document: '', birth_date: '', email: '', phone: '' }

/** The company enrolls an employee in one of its agreements. */
export default function PortalEnroll() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [form, setForm] = useState(empty)
  const [agreements, setAgreements] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    api('/portal/agreements')
      .then((rows) => {
        if (!active) return
        const open = rows.filter((item) => ['in_force', 'scheduled'].includes(item.status))
        setAgreements(open)
        const chosen = open.find((item) => String(item.id) === params.get('agreement')) || (open.length === 1 ? open[0] : null)
        if (chosen) choose(chosen)
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function choose(agreement) {
    setForm((current) => ({
      ...current,
      agreement_id: agreement ? String(agreement.id) : '',
      modality_id: agreement && agreement.plans.length === 1 ? String(agreement.plans[0].modality_id) : '',
      joined_on: agreement ? (agreement.starts_on > isoDate(today()) ? agreement.starts_on : isoDate(today())) : '',
    }))
  }

  const agreement = agreements.find((item) => String(item.id) === form.agreement_id)
  const plan = agreement ? agreement.plans.find((item) => String(item.modality_id) === form.modality_id) : null
  // Co-payment: the split agreed with this employee (blank: the plan's or the agreement's default).
  const shared = Boolean(agreement && agreement.payer === 'shared')
  const defaultShare = plan && plan.company_share_percent != null ? plan.company_share_percent : agreement ? agreement.company_share_percent : 0
  const share = shared && form.company_share_percent !== '' ? Number(form.company_share_percent) : defaultShare
  const companyPart = plan ? Math.floor((plan.value_cents * share + 50) / 100) : 0
  const earliest = agreement ? (agreement.starts_on > isoDate(today()) ? agreement.starts_on : isoDate(today())) : isoDate(today())

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api('/portal/employees', {
        method: 'POST',
        message: `${form.name} enrolled — the employee is told by email`,
        body: {
          ...form,
          agreement_id: Number(form.agreement_id),
          modality_id: Number(form.modality_id),
          birth_date: form.birth_date || null,
          joined_on: form.joined_on || null,
          company_share_percent: shared && form.company_share_percent !== '' ? Number(form.company_share_percent) : null,
        },
      })
      navigate(`/my-employees?agreement=${form.agreement_id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/my-employees', label: 'Employees' }}
        title="Enroll an employee"
        hint="The employee is identified by CPF: an existing Santé Saúde customer keeps its record (the name must match it). The plan runs until the end of the contract."
      />
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <fieldset>
            <legend>Plan</legend>
            <label>
              Agreement *
              <select
                value={form.agreement_id}
                required
                onChange={(event) => choose(agreements.find((item) => String(item.id) === event.target.value))}
              >
                <option value="">Select</option>
                {agreements.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} ({formatDate(item.starts_on)} – {formatDate(item.ends_on)})</option>
                ))}
              </select>
              {agreements.length === 0 && <small className="hint">No agreement takes enrollments now: contact Santé Saúde.</small>}
            </label>
            <label>
              Plan *
              <select value={form.modality_id} required disabled={!agreement} onChange={(event) => set('modality_id', event.target.value)}>
                <option value="">Select</option>
                {(agreement ? agreement.plans : []).map((item) => (
                  <option key={item.modality_id} value={item.modality_id}>
                    {item.modality_name} — {money(item.value_cents)}/month ({item.discount_percent}% off)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Starts on *
              <input
                type="date"
                value={form.joined_on}
                required
                min={earliest}
                max={agreement ? agreement.ends_on : undefined}
                onChange={(event) => set('joined_on', event.target.value)}
              />
              {agreement && <small className="hint">Until {formatDate(agreement.ends_on)}, the end of the contract.</small>}
            </label>
            {shared && (
              <label>
                Company's part of the fees (%)
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={form.company_share_percent}
                  placeholder={String(defaultShare)}
                  onChange={(event) => set('company_share_percent', event.target.value)}
                />
                <small className="hint">As agreed with the employee; blank: {defaultShare}% (the contract's default). It may change later.</small>
              </label>
            )}
          </fieldset>
          <fieldset>
            <legend>Employee</legend>
            <label>
              Full name *
              <input value={form.name} required maxLength={120} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              CPF *
              <input value={form.document} required maxLength={20} placeholder="000.000.000-00" onChange={(event) => set('document', event.target.value)} />
            </label>
            <label>
              Birth date
              <input type="date" value={form.birth_date} max={isoDate(today())} onChange={(event) => set('birth_date', event.target.value)} />
            </label>
            <label>
              Email
              <input type="email" value={form.email} maxLength={180} onChange={(event) => set('email', event.target.value)} />
              <small className="hint">The employee receives the welcome and the plan's notices here.</small>
            </label>
            <label>
              Phone
              <input value={form.phone} maxLength={30} onChange={(event) => set('phone', event.target.value)} />
            </label>
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="submit" disabled={saving || !agreement}>Enroll</button>
            <Link className="button ghost" to="/my-employees">Cancel</Link>
          </div>
        </form>
        <div className="stack">
          {agreement && (
            <div className="card padded status-panel" data-testid="enroll-preview">
              <h2>{agreement.name}</h2>
              <dl>
                <dt>Monthly fee</dt>
                <dd>
                  {plan ? (
                    <>
                      <strong>{money(plan.value_cents)}</strong> <small className="muted">({plan.discount_percent}% off {money(plan.plan_value_cents)}; extra dependents above the plan's limit are added)</small>
                    </>
                  ) : (
                    <span className="muted">Choose the plan</span>
                  )}
                </dd>
                {plan && (
                  <>
                    <dt>Waiting period</dt>
                    <dd>{graceLabel(plan.grace_days, plan.plan_grace_days)}</dd>
                  </>
                )}
                <dt>Payer</dt>
                <dd>{payerLabel(agreement.payer, shared ? share : agreement.company_share_percent)}</dd>
                {shared && plan && (
                  <>
                    <dt>Each month</dt>
                    <dd data-testid="enroll-split">
                      the company <strong>{money(companyPart)}</strong> · the employee <strong>{money(plan.value_cents - companyPart)}</strong>
                    </dd>
                  </>
                )}
                <dt>This month</dt>
                <dd>
                  <span className={`badge ${agreement.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(agreement.employees, agreement.min_employees)}</span>
                  {!agreement.conditions_met && (
                    <small className="muted"> · {agreement.missing_employees} short: below the minimum your company pays the month's discounts back</small>
                  )}
                </dd>
              </dl>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
