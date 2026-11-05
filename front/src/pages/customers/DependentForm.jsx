import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import PersonFields, { KINSHIPS, emptyPerson, personToBody, personToForm } from '../../components/PersonFields'

const empty = { ...emptyPerson, kinship: '' }

export default function DependentForm() {
  const { customerId, dependentId } = useParams()
  const navigate = useNavigate()
  const [holder, setHolder] = useState(null)
  const [form, setForm] = useState(empty)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const back = `/customers/${customerId}`

  useEffect(() => {
    setError('')
    const requests = [api(`/customers/${customerId}`)]
    if (dependentId) requests.push(api(`/customers/${customerId}/dependents/${dependentId}`))
    Promise.all(requests)
      .then(([owner, row]) => {
        setHolder(owner)
        if (row) setForm({ ...personToForm(row), kinship: row.kinship || '' })
        else {
          // Dependents usually share the holder's address.
          setForm({
            ...empty,
            street: owner.street,
            number: owner.number,
            complement: owner.complement,
            district: owner.district,
            city: owner.city,
            state: owner.state,
            zip_code: owner.zip_code,
          })
        }
      })
      .catch((err) => setError(err.message))
  }, [customerId, dependentId])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api(
        dependentId ? `/customers/${customerId}/dependents/${dependentId}` : `/customers/${customerId}/dependents`,
        { method: dependentId ? 'PUT' : 'POST', body: { ...personToBody(form), kinship: form.kinship } },
      )
      navigate(back)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: back, label: holder ? holder.name : 'Customer' }}
        title={dependentId ? `Edit dependent` : 'New dependent'}
        hint={holder ? `Dependent of ${holder.name}` : ''}
      />
      <form className="panel form-page" onSubmit={save}>
        <PersonFields form={form} set={set} requireBirthDate>
          <label>
            Kinship
            <select value={form.kinship} onChange={(event) => set('kinship', event.target.value)}>
              <option value="">—</option>
              {KINSHIPS.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
        </PersonFields>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={saving}>{dependentId ? 'Save' : 'Create'}</button>
          <Link className="button ghost" to={back}>Cancel</Link>
        </div>
      </form>
    </section>
  )
}
