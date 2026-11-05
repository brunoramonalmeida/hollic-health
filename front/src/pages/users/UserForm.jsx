import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { ROLE_LABEL, useSession } from '../../session'

const empty = { name: '', email: '', role: 'client', password: '', customer_id: '', professional_id: '', seller_id: '', partner_id: '' }

export default function UserForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const [form, setForm] = useState(empty)
  const [customers, setCustomers] = useState([])
  const [professionals, setProfessionals] = useState([])
  const [sellers, setSellers] = useState([])
  const [partners, setPartners] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  // Supervisors manage the external logins only: clients and partner companies.
  const roles = session.canManageStaffUsers ? Object.keys(ROLE_LABEL) : ['client', 'partner']

  useEffect(() => {
    setError('')
    // Sellers are listed for admins (the only ones who create seller logins).
    const requests = [
      api('/customers'),
      api('/professionals'),
      session.canManageStaffUsers ? api('/sellers') : Promise.resolve([]),
      api('/partners'),
    ]
    if (id) requests.push(api(`/users/${id}`))
    Promise.all(requests)
      .then(([people, staff, team, companies, row]) => {
        setCustomers(people)
        setProfessionals(staff)
        setSellers(team)
        setPartners(companies)
        // Only an existing user replaces the form: a new one keeps whatever was typed while loading.
        if (row) {
          setForm({
            name: row.name,
            email: row.email,
            role: row.role,
            password: '',
            customer_id: row.customer_id ? String(row.customer_id) : '',
            professional_id: row.professional_id ? String(row.professional_id) : '',
            seller_id: row.seller_id ? String(row.seller_id) : '',
            partner_id: row.partner_id ? String(row.partner_id) : '',
          })
        }
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
      await api(id ? `/users/${id}` : '/users', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'User updated' : 'User created — a welcome message was sent',
        body: {
          name: form.name,
          email: form.email,
          role: form.role,
          password: form.password || null,
          customer_id: form.role === 'client' ? Number(form.customer_id) || null : null,
          professional_id: form.role === 'professional' ? Number(form.professional_id) || null : null,
          seller_id: form.role === 'seller' ? Number(form.seller_id) || null : null,
          partner_id: form.role === 'partner' ? Number(form.partner_id) || null : null,
        },
      })
      navigate('/users')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader back={{ to: '/users', label: 'Users' }} title={id ? 'Edit user' : 'New user'} />
      <form className="panel form-page" onSubmit={save}>
        <div className="fields">
          <label className="span-2">
            Name *
            <input value={form.name} required onChange={(event) => set('name', event.target.value)} />
          </label>
          <label className="span-2">
            Email *
            <input type="email" value={form.email} required onChange={(event) => set('email', event.target.value)} />
          </label>
          <label>
            Role *
            <select value={form.role} onChange={(event) => set('role', event.target.value)}>
              {roles.map((role) => <option key={role} value={role}>{ROLE_LABEL[role]}</option>)}
            </select>
          </label>
          {form.role === 'client' && (
            <label className="span-2">
              Customer *
              <select value={form.customer_id} required onChange={(event) => set('customer_id', event.target.value)}>
                <option value="">Select</option>
                {customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <small className="hint">The client may only act on this customer's appointments and plan.</small>
            </label>
          )}
          {form.role === 'professional' && (
            <label className="span-2">
              Professional *
              <select value={form.professional_id} required onChange={(event) => set('professional_id', event.target.value)}>
                <option value="">Select</option>
                {professionals.map((item) => <option key={item.id} value={item.id}>{item.name} — {item.specialty_name}</option>)}
              </select>
              <small className="hint">The professional signs the appointments of this agenda and sees its own payouts.</small>
            </label>
          )}
          {form.role === 'seller' && (
            <label className="span-2">
              Seller *
              <select value={form.seller_id} required onChange={(event) => set('seller_id', event.target.value)}>
                <option value="">Select</option>
                {sellers.map((item) => <option key={item.id} value={item.id}>{item.name} — {item.region_name}{item.active ? '' : ' (inactive)'}</option>)}
              </select>
              <small className="hint">The seller records its own sales, manages its portfolio of customers and follows its commissions.</small>
            </label>
          )}
          {form.role === 'partner' && (
            <label className="span-2">
              Partner company *
              <select value={form.partner_id} required onChange={(event) => set('partner_id', event.target.value)}>
                <option value="">Select</option>
                {partners.map((item) => <option key={item.id} value={item.id}>{item.name}{item.active ? '' : ' (inactive)'}</option>)}
              </select>
              <small className="hint">The company portal: enroll and remove the company's employees, follow its agreements and invoices. Nothing else of the network.</small>
            </label>
          )}
          <label>
            {id ? 'New password' : 'Password *'}
            <input
              type="password"
              value={form.password}
              required={!id}
              minLength={6}
              autoComplete="new-password"
              onChange={(event) => set('password', event.target.value)}
            />
            {id && <small className="hint">Leave blank to keep the current password.</small>}
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>
          <Link className="button ghost" to="/users">Cancel</Link>
        </div>
      </form>
    </section>
  )
}
