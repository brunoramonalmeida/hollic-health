import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api'
import AddressFields, { emptyAddress } from '../../components/AddressFields'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

const empty = { name: '', cnpj: '', ibge_code: '', tax_regime: '', municipal_registration: '', phone: '', latitude: null, longitude: null, ...emptyAddress }

export default function CompanyForm() {
  const navigate = useNavigate()
  const session = useSession()
  const readOnly = session.role !== 'admin'
  const [form, setForm] = useState(empty)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/company').then((row) => setForm({ ...empty, ...row })).catch((err) => setError(err.message))
  }, [])

  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }))

  async function save(event) {
    event.preventDefault()
    setError('')
    try {
      const { id, address, maps_url, osm_url, ...body } = form
      await api('/company', { method: 'PUT', body, message: 'Company settings saved' })
      navigate('/tax-rules')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/tax-rules', label: 'Tax rules' }}
        title="Company settings"
        hint="Head office of the network: used as the jurisdiction for taxes on plan fees and as the header of the accounting reports."
      />
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Identification</legend>
          <div className="fields">
            <label className="span-2">
              Company name *
              <input value={form.name} required onChange={(e) => set('name', e.target.value)} />
            </label>
            <label>
              CNPJ
              <input value={form.cnpj} placeholder="00.000.000/0001-00" onChange={(e) => set('cnpj', e.target.value)} />
            </label>
            <label>
              Tax regime
              <input value={form.tax_regime} placeholder="Lucro Presumido" onChange={(e) => set('tax_regime', e.target.value)} />
            </label>
            <label>
              Municipal registration
              <input value={form.municipal_registration} onChange={(e) => set('municipal_registration', e.target.value)} />
            </label>
          </div>
        </fieldset>
        <AddressFields form={form} set={set} withCoordinates disabled={readOnly} />
        <fieldset disabled={readOnly}>
          <legend>Tax jurisdiction</legend>
          <div className="fields">
            <label>
              IBGE code
              <input value={form.ibge_code} maxLength={7} placeholder="3550308" onChange={(e) => set('ibge_code', e.target.value)} />
              <small className="hint">Municipality of the head office, used for taxes on plan fees.</small>
            </label>
            <label>
              Phone
              <input value={form.phone} onChange={(e) => set('phone', e.target.value)} />
            </label>
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit">Save</button>}
          <Link className="button ghost" to="/tax-rules">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
    </section>
  )
}
