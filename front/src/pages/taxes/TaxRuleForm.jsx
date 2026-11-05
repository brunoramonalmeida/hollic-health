import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { STATES } from '../../br'
import { useSession } from '../../session'
import { APPLIES_LABEL, LEVEL_LABEL } from './TaxRuleList'

const empty = {
  code: '',
  name: '',
  level: 'municipal',
  rate: '',
  applies_to: 'all',
  base: 'amount',
  state: '',
  city: '',
  clinic_id: '',
  specialty_id: '',
  modality_id: '',
  effective_from: '',
  effective_to: '',
  active: true,
  notes: '',
}

export default function TaxRuleForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const readOnly = session.role !== 'admin'
  const [form, setForm] = useState(empty)
  const [clinics, setClinics] = useState([])
  const [specialties, setSpecialties] = useState([])
  const [plans, setPlans] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const requests = [api('/clinics'), api('/specialties'), api('/modalities')]
    if (id) requests.push(api(`/tax-rules/${id}`))
    Promise.all(requests)
      .then(([c, s, p, row]) => {
        setClinics(c)
        setSpecialties(s)
        setPlans(p)
        if (row) {
          setForm({
            code: row.code,
            name: row.name,
            level: row.level,
            rate: (row.rate_bp / 100).toFixed(2),
            applies_to: row.applies_to,
            base: row.base,
            state: row.state || '',
            city: row.city || '',
            clinic_id: row.clinic_id ? String(row.clinic_id) : '',
            specialty_id: row.specialty_id ? String(row.specialty_id) : '',
            modality_id: row.modality_id ? String(row.modality_id) : '',
            effective_from: row.effective_from,
            effective_to: row.effective_to || '',
            active: row.active,
            notes: row.notes || '',
          })
        } else {
          setForm({ ...empty, effective_from: new Date().toISOString().slice(0, 10) })
        }
      })
      .catch((err) => setError(err.message))
  }, [id])

  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }))

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const body = {
        code: form.code,
        name: form.name,
        level: form.level,
        rate_bp: Math.round(Number(String(form.rate).replace(',', '.')) * 100),
        applies_to: form.applies_to,
        base: form.base,
        state: form.state || null,
        city: form.city || null,
        clinic_id: form.clinic_id ? Number(form.clinic_id) : null,
        specialty_id: form.specialty_id ? Number(form.specialty_id) : null,
        modality_id: form.modality_id ? Number(form.modality_id) : null,
        effective_from: form.effective_from,
        effective_to: form.effective_to || null,
        active: form.active,
        notes: form.notes,
      }
      await api(id ? `/tax-rules/${id}` : '/tax-rules', { method: id ? 'PUT' : 'POST', body, message: id ? 'Tax rule updated' : 'Tax rule created' })
      navigate('/tax-rules')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/tax-rules', label: 'Tax rules' }}
        title={id ? `${form.code} · ${form.rate}%` : 'New tax rule'}
        hint="Leave a scope field empty to mean “any”. A rule with more scope fields set beats a more general one for the same tax code."
      />
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Tax</legend>
          <div className="fields">
            <label>
              Code *
              <input value={form.code} required maxLength={20} placeholder="ISS" onChange={(e) => set('code', e.target.value.toUpperCase())} />
            </label>
            <label className="span-2">
              Name *
              <input value={form.name} required placeholder="Imposto Sobre Serviços" onChange={(e) => set('name', e.target.value)} />
            </label>
            <label>
              Level *
              <select value={form.level} onChange={(e) => set('level', e.target.value)}>
                {Object.entries(LEVEL_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>
              Rate (%) *
              <input value={form.rate} required inputMode="decimal" placeholder="5.00" onChange={(e) => set('rate', e.target.value)} />
              <small className="hint">Up to two decimals (e.g. 0.65).</small>
            </label>
            <label>
              Applies to
              <select value={form.applies_to} onChange={(e) => set('applies_to', e.target.value)}>
                {Object.entries(APPLIES_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>
              Calculation base
              <select value={form.base} onChange={(e) => set('base', e.target.value)}>
                <option value="amount">Billed amount (after plan discount)</option>
                <option value="gross">Gross price (before discount)</option>
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} />
              Active
            </label>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Scope</legend>
          <div className="fields">
            <label>
              State
              <select value={form.state} onChange={(e) => set('state', e.target.value)}>
                <option value="">Any</option>
                {STATES.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
              </select>
            </label>
            <label>
              Municipality
              <input value={form.city} placeholder="Any" onChange={(e) => set('city', e.target.value)} />
              <small className="hint">Matched against the clinic's city (or the company's for plan fees).</small>
            </label>
            <label>
              Clinic
              <select value={form.clinic_id} onChange={(e) => set('clinic_id', e.target.value)}>
                <option value="">Any</option>
                {clinics.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label>
              Specialty
              <select value={form.specialty_id} onChange={(e) => set('specialty_id', e.target.value)}>
                <option value="">Any</option>
                {specialties.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label>
              Plan
              <select value={form.modality_id} onChange={(e) => set('modality_id', e.target.value)}>
                <option value="">Any</option>
                {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Validity</legend>
          <div className="fields">
            <label>
              Effective from *
              <input type="date" value={form.effective_from} required onChange={(e) => set('effective_from', e.target.value)} />
            </label>
            <label>
              Effective to
              <input type="date" value={form.effective_to} min={form.effective_from} onChange={(e) => set('effective_to', e.target.value)} />
              <small className="hint">Leave empty while the rate is current. To change a rate, close this rule and create a new one.</small>
            </label>
            <label className="span-2">
              Notes
              <input value={form.notes} onChange={(e) => set('notes', e.target.value)} />
            </label>
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
          <Link className="button ghost" to="/tax-rules">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
    </section>
  )
}
