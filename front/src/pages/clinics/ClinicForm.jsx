import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fromCents, toCents } from '../../api'
import AddressFields, { emptyAddress } from '../../components/AddressFields'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

const empty = { name: '', cnpj: '', ibge_code: '', phone: '', directions: '', fee: '0.00', on_call_fee: '', remote_fee: '', daily_limit: 0, monthly_limit: 0, latitude: null, longitude: null, ...emptyAddress }

export default function ClinicForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { canEditPricing } = useSession()
  const readOnly = !canEditPricing
  const [form, setForm] = useState(empty)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!id) {
      setForm(empty)
      return
    }
    api(`/clinics/${id}`)
      .then((row) =>
        setForm({
          ...empty,
          ...row,
          fee: fromCents(row.fee_cents),
          on_call_fee: row.on_call_fee_cents == null ? '' : fromCents(row.on_call_fee_cents),
          remote_fee: row.remote_fee_cents == null ? '' : fromCents(row.remote_fee_cents),
        }),
      )
      .catch((err) => setError(err.message))
  }, [id])

  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }))

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const { fee, on_call_fee, remote_fee, id: _id, address, maps_url, osm_url, fee_cents, on_call_fee_cents, remote_fee_cents, ...rest } = form
      await api(id ? `/clinics/${id}` : '/clinics', {
        method: id ? 'PUT' : 'POST',
        body: {
          ...rest,
          fee_cents: toCents(fee),
          on_call_fee_cents: String(on_call_fee).trim() === '' ? null : toCents(on_call_fee),
          remote_fee_cents: String(remote_fee).trim() === '' ? null : toCents(remote_fee),
          daily_limit: Number(form.daily_limit || 0),
          monthly_limit: Number(form.monthly_limit || 0),
        },
        message: id ? 'Clinic updated' : 'Clinic created',
      })
      navigate('/clinics')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader back={{ to: '/clinics', label: 'Clinics' }} title={id ? form.name || 'Clinic' : 'New clinic'} hint="Fill the address and locate it on the map so patients can find the place." />
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Clinic</legend>
          <div className="fields">
            <label className="span-2">
              Name *
              <input value={form.name} required onChange={(e) => set('name', e.target.value)} />
            </label>
            <label>
              CNPJ
              <input value={form.cnpj} placeholder="00.000.000/0001-00" onChange={(e) => set('cnpj', e.target.value)} />
            </label>
            <label>
              Phone
              <input value={form.phone} placeholder="(00) 0000-0000" onChange={(e) => set('phone', e.target.value)} />
            </label>
            <label>
              Clinic fee (R$)
              <input value={form.fee} inputMode="decimal" onChange={(e) => set('fee', e.target.value)} />
              <small className="hint">Added to every appointment held here.</small>
            </label>
            <label>
              On-call fee (R$)
              <input value={form.on_call_fee} inputMode="decimal" placeholder="Same as the clinic fee" onChange={(e) => set('on_call_fee', e.target.value)} />
              <small className="hint">Charged instead of the clinic fee on on-call services; never below it. Leave blank to charge the same.</small>
            </label>
            <label>
              Remote fee (R$)
              <input value={form.remote_fee} inputMode="decimal" placeholder="Same as the clinic fee" onChange={(e) => set('remote_fee', e.target.value)} />
              <small className="hint">Charged instead of the clinic fee on remote appointments (video call), where no room is used — may be 0. Leave blank to charge the same.</small>
            </label>
            <label>
              IBGE municipality code
              <input value={form.ibge_code} maxLength={7} placeholder="3550308" onChange={(e) => set('ibge_code', e.target.value)} />
              <small className="hint">Used by the municipal tax rules and reports.</small>
            </label>
            <label>
              Max appointments per day
              <input type="number" min={0} value={form.daily_limit} onChange={(e) => set('daily_limit', e.target.value)} />
            </label>
            <label>
              Max appointments per month
              <input type="number" min={0} value={form.monthly_limit} onChange={(e) => set('monthly_limit', e.target.value)} />
            </label>
            <p className="notice info span-2">
              Service capacity reserved for Santé Saúde across every professional of the clinic — the clinic also
              serves patients outside the network. 0 = unlimited. At the limit, the agendas of its professionals show
              the day as full and no further appointment can be booked here.
            </p>
            <label className="span-2">
              How to get there
              <textarea value={form.directions} placeholder="Entrance, floor, parking, nearest station…" onChange={(e) => set('directions', e.target.value)} />
            </label>
          </div>
        </fieldset>
        <AddressFields form={form} set={set} withCoordinates cityRequired disabled={readOnly} />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
          <Link className="button ghost" to="/clinics">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
    </section>
  )
}
