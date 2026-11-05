import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import PersonFields, { emptyPerson, personToBody, personToForm } from '../../components/PersonFields'
import { ageOf, formatDate } from '../../dates'
import { useSession } from '../../session'

export default function CustomerForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const [form, setForm] = useState(emptyPerson)
  const [dependents, setDependents] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function loadDependents() {
    setDependents(await api(`/customers/${id}/dependents`))
  }

  useEffect(() => {
    setError('')
    if (!id) {
      setForm(emptyPerson)
      setDependents([])
      return
    }
    Promise.all([api(`/customers/${id}`), api(`/customers/${id}/dependents`)])
      .then(([row, deps]) => {
        setForm(personToForm(row))
        setDependents(deps)
      })
      .catch((err) => setError(err.message))
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const saved = await api(id ? `/customers/${id}` : '/customers', {
        method: id ? 'PUT' : 'POST',
        body: personToBody(form),
      })
      // A new holder usually has dependents to register next: stay on the record.
      navigate(id ? '/customers' : `/customers/${saved.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function removeDependent(item) {
    if (!window.confirm(`Delete dependent "${item.name}"?`)) return
    setError('')
    try {
      await api(`/customers/${id}/dependents/${item.id}`, { method: 'DELETE' })
      await loadDependents()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/customers', label: session.isSeller ? 'My customers' : 'Customers' }}
        title={id ? form.name || 'Customer' : 'New customer'}
        hint={session.isSeller ? 'City and state are needed to sell: they must be inside the scope of your region.' : undefined}
      >
        {id && session.isSeller && <Link className="button" to={`/subscriptions/new?customer=${id}`}>Sell a plan</Link>}
        {id && session.isStaff && <Link className="button ghost" to={`/customers/${id}/payments`}>Payments</Link>}
      </PageHeader>
      <div className="stack">
        <form className="panel form-page" onSubmit={save}>
          <PersonFields form={form} set={set} />
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>
            <Link className="button ghost" to="/customers">Cancel</Link>
          </div>
        </form>
        {id && (
          <div className="card">
            <div className="card-head">
              <h2>Dependents</h2>
              <Link className="button" to={`/customers/${id}/dependents/new`}>Add dependent</Link>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Kinship</th>
                  <th>Birth date</th>
                  <th>Age</th>
                  <th>CNS</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {dependents.map((item) => (
                  <tr key={item.id}>
                    <td>{item.name}</td>
                    <td>{item.kinship}</td>
                    <td>{formatDate(item.birth_date)}</td>
                    <td>{ageOf(item.birth_date)}</td>
                    <td>{item.health_card}</td>
                    <td className="actions">
                      <Link className="button ghost" to={`/customers/${id}/dependents/${item.id}`}>Edit</Link>
                      <button type="button" className="warn" onClick={() => removeDependent(item)}>Delete</button>
                    </td>
                  </tr>
                ))}
                {dependents.length === 0 && <tr><td colSpan={6} className="muted">No dependents.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
