import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fromCents, money, toCents } from '../../api'
import PageHeader from '../../components/PageHeader'
import WeekHoursEditor, { flatten, groupByDay } from '../../components/WeekHoursEditor'
import { WEEKDAYS } from '../../dates'
import { useSession } from '../../session'

const empty = {
  name: '',
  document: '',
  specialty_id: '',
  clinic_id: '',
  price: '0.00',
  slot_minutes: 30,
  daily_limit: '0',
  monthly_limit: '0',
  on_call_enabled: false,
  on_call_price: '0.00',
  urgent_enabled: false,
  urgent_price: '0.00',
  remote_enabled: false,
  remote_price: '',
}

export default function ProfessionalForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { canEditPricing } = useSession()
  const readOnly = !canEditPricing
  const [form, setForm] = useState(empty)
  const [days, setDays] = useState(WEEKDAYS.map(() => []))
  const [onCallDays, setOnCallDays] = useState(WEEKDAYS.map(() => []))
  const [defaults, setDefaults] = useState(null)
  const [specialties, setSpecialties] = useState([])
  const [clinics, setClinics] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    const requests = [api('/specialties'), api('/clinics'), api('/professionals/default-week')]
    if (id) requests.push(api(`/professionals/${id}`))
    Promise.all(requests)
      .then(([specs, places, week, row]) => {
        setSpecialties(specs)
        setClinics(places)
        setDefaults(week)
        if (row) {
          setForm({
            name: row.name,
            document: row.document || '',
            specialty_id: String(row.specialty_id),
            clinic_id: String(row.clinic_id),
            price: fromCents(row.price_cents),
            slot_minutes: row.slot_minutes,
            daily_limit: String(row.daily_limit || 0),
            monthly_limit: String(row.monthly_limit || 0),
            on_call_enabled: Boolean(row.on_call_enabled),
            on_call_price: fromCents(row.on_call_price_cents),
            urgent_enabled: Boolean(row.urgent_enabled),
            urgent_price: fromCents(row.urgent_price_cents),
            remote_enabled: Boolean(row.remote_enabled),
            remote_price: row.remote_price_cents == null ? '' : fromCents(row.remote_price_cents),
          })
          setDays(groupByDay(row.working_hours))
          setOnCallDays(groupByDay(row.on_call_hours || []))
        } else {
          // New professionals start from the default week and adjust it.
          setForm({ ...empty, slot_minutes: week.slot_minutes })
          setDays(groupByDay(week.working_hours))
          setOnCallDays(WEEKDAYS.map(() => []))
        }
      })
      .catch((err) => setError(err.message))
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const onCallRateWarning =
    form.on_call_enabled && toCents(form.on_call_price) <= toCents(form.price) ? 'Set it above the regular fee.' : ''
  const urgentMinimum = 3 * toCents(form.price)
  const urgentRateWarning =
    form.urgent_enabled && toCents(form.urgent_price) < urgentMinimum ? `Set it to ${money(urgentMinimum)} or more.` : ''

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const body = {
        name: form.name,
        document: form.document.trim(),
        specialty_id: Number(form.specialty_id),
        clinic_id: Number(form.clinic_id),
        price_cents: toCents(form.price),
        slot_minutes: Number(form.slot_minutes),
        daily_limit: Number(form.daily_limit || 0),
        monthly_limit: Number(form.monthly_limit || 0),
        on_call_enabled: form.on_call_enabled,
        on_call_price_cents: form.on_call_enabled ? toCents(form.on_call_price) : 0,
        urgent_enabled: form.urgent_enabled,
        urgent_price_cents: form.urgent_enabled ? toCents(form.urgent_price) : 0,
        remote_enabled: form.remote_enabled,
        remote_price_cents: String(form.remote_price).trim() === '' ? null : toCents(form.remote_price),
        working_hours: flatten(days),
        on_call_hours: form.on_call_enabled ? flatten(onCallDays) : [],
      }
      await api(id ? `/professionals/${id}` : '/professionals', { method: id ? 'PUT' : 'POST', body, message: id ? 'Professional updated' : 'Professional created' })
      navigate('/professionals')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/professionals', label: 'Professionals' }}
        title={id ? form.name || 'Professional' : 'New professional'}
        hint="Working hours are cut into slots of the chosen duration; each slot takes one appointment, up to the professional's and the clinic's service capacity."
      >
        {id && <Link className="button ghost" to={`/professionals/${id}/agenda`}>Open agenda</Link>}
      </PageHeader>
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Record</legend>
          <div className="fields">
            <label>
              Name *
              <input value={form.name} required onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              CPF
              <input value={form.document} maxLength={20} placeholder="000.000.000-00" onChange={(event) => set('document', event.target.value)} />
              <small className="hint">The ICP-Brasil certificate the professional signs prescriptions with must carry this CPF.</small>
            </label>
            <label>
              Specialty *
              <select value={form.specialty_id} required onChange={(event) => set('specialty_id', event.target.value)}>
                <option value="">Select</option>
                {specialties.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label>
              Clinic *
              <select value={form.clinic_id} required onChange={(event) => set('clinic_id', event.target.value)}>
                <option value="">Select</option>
                {clinics.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label>
              Professional fee
              <input value={form.price} inputMode="decimal" onChange={(event) => set('price', event.target.value)} />
              <small className="hint">Added to the clinic fee and specialty price at scheduling.</small>
            </label>
            <label>
              Slot duration (minutes)
              <input
                type="number"
                min={5}
                max={240}
                step={5}
                value={form.slot_minutes}
                onChange={(event) => set('slot_minutes', event.target.value)}
              />
            </label>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Service capacity</legend>
          <div className="fields">
            <label>
              Max appointments per day
              <input type="number" min={0} value={form.daily_limit} onChange={(event) => set('daily_limit', event.target.value)} />
            </label>
            <label>
              Max appointments per month
              <input type="number" min={0} value={form.monthly_limit} onChange={(event) => set('monthly_limit', event.target.value)} />
            </label>
            <p className="notice info span-2">
              How much of the agenda is reserved for Santé Saúde — professionals also serve patients outside the
              network. 0 = unlimited. When a day or the calendar month reaches the limit, the agenda shows it as full
              and no further appointment can be booked (already-booked ones stand; cancelling frees the place). The
              clinic's own capacity applies on top of this one.
            </p>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Weekly working hours</legend>
          <div className="actions" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="ghost"
              disabled={!defaults}
              onClick={() => defaults && setDays(groupByDay(defaults.working_hours))}
            >
              Use default week
            </button>
            <button type="button" className="ghost" onClick={() => setDays(WEEKDAYS.map(() => []))}>
              Clear all
            </button>
            {defaults && (
              <span className="muted">
                Default: Monday to Friday, 08:00–12:00 and 13:00–17:00.
              </span>
            )}
          </div>
          <WeekHoursEditor days={days} onChange={setDays} />
          <p className="muted">
            Days or hours the professional does not want to work on specific dates are registered as time off in the agenda.
          </p>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>On-call</legend>
          <div className="fields">
            <label className="check span-2">
              <input type="checkbox" checked={form.on_call_enabled} onChange={(event) => set('on_call_enabled', event.target.checked)} />
              Available for on-call
            </label>
            {form.on_call_enabled && (
              <label>
                On-call fee
                <input value={form.on_call_price} inputMode="decimal" required onChange={(event) => set('on_call_price', event.target.value)} />
                <small className="hint">
                  Paid for every appointment in an on-call shift; must be greater than the regular fee{form.price ? ` (${money(toCents(form.price))})` : ''}.
                  {onCallRateWarning && <strong className="warn-text"> {onCallRateWarning}</strong>}
                </small>
              </label>
            )}
            <p className="notice info span-2">
              {form.on_call_enabled
                ? 'Appointments booked in the on-call shifts below are on-call services: they use the on-call price list — this fee, the clinic\'s on-call fee and the specialty\'s on-call price (when set) — and the plan discount applies as usual. Shifts must not overlap the regular working hours.'
                : 'Not available for on-call: only the regular working hours are offered in the agenda.'}
            </p>
          </div>
          {form.on_call_enabled && (
            <WeekHoursEditor days={onCallDays} onChange={setOnCallDays} emptyLabel="No shift" defaultRange={{ start: '19:00', end: '22:00' }} />
          )}
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Urgent visits</legend>
          <div className="fields">
            <label className="check span-2">
              <input type="checkbox" checked={form.urgent_enabled} onChange={(event) => set('urgent_enabled', event.target.checked)} />
              Takes ad-hoc urgent visits
            </label>
            {form.urgent_enabled && (
              <label>
                Urgent rate
                <input value={form.urgent_price} inputMode="decimal" required onChange={(event) => set('urgent_price', event.target.value)} />
                <small className="hint">
                  Paid instead of the fee on urgent visits; at least three times the fee ({money(urgentMinimum)}).
                  {urgentRateWarning && <strong className="warn-text"> {urgentRateWarning}</strong>}
                </small>
              </label>
            )}
            <p className="notice info span-2">
              The professional flags this availability in its own profile too. Urgent visits are booked at any time — inside or
              outside the working hours — except during the on-call shifts, where they are never accepted. Clinic fee and
              specialty price stay the regular ones; the plan discount applies as usual.
            </p>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Remote appointments</legend>
          <div className="fields">
            <label className="check span-2">
              <input type="checkbox" checked={form.remote_enabled} onChange={(event) => set('remote_enabled', event.target.checked)} />
              Offers remote appointments (video call)
            </label>
            {form.remote_enabled && (
              <label>
                Remote fee
                <input
                  value={form.remote_price}
                  inputMode="decimal"
                  placeholder="Same as the fee"
                  onChange={(event) => set('remote_price', event.target.value)}
                />
                <small className="hint">
                  Paid instead of the fee on remote appointments. Leave blank to pay the regular fee{form.price ? ` (${money(toCents(form.price))})` : ''}.
                </small>
              </label>
            )}
            <p className="notice info span-2">
              {form.remote_enabled
                ? 'The professional flags this in its own profile too. Remote appointments are booked in the regular working hours like in-person ones (never in the on-call shifts, never as urgent visits) and use the remote price list — this fee, the clinic\'s remote fee and the specialty\'s remote price (when set); the plan discount applies as usual. Each one gets a video-call link sent to the patient and the professional.'
                : 'In person only: the agenda takes no remote appointments for this professional.'}
            </p>
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
          <Link className="button ghost" to="/professionals">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
    </section>
  )
}
