import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { SUBSCRIPTION_STATUS, statusLabel } from '../../labels'
import { useSession } from '../../session'

export default function CustomerList() {
  const session = useSession()
  const [rows, setRows] = useState([])
  const [plans, setPlans] = useState({})
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')

  async function load() {
    const [people, subscriptions] = await Promise.all([api('/customers'), api('/subscriptions')])
    setRows(people)
    // Current (non-cancelled) subscription per customer; cancelled ones are kept only as fallback.
    const byCustomer = {}
    subscriptions.forEach((item) => {
      const current = byCustomer[item.customer_id]
      if (!current || (current.status === 'cancelada' && item.status !== 'cancelada')) byCustomer[item.customer_id] = item
    })
    setPlans(byCustomer)
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete "${row.name}" and their dependents?`)) return
    setError('')
    try {
      await api(`/customers/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const term = search.trim().toLowerCase()
  const visible = term
    ? rows.filter((row) => [row.name, row.document, row.phone, row.email].join(' ').toLowerCase().includes(term))
    : rows

  return (
    <section>
      <PageHeader
        crumb={session.isSeller ? 'My area / Customers' : 'Care / Customers'}
        title={session.isSeller ? 'My customers' : 'Customers'}
        hint={
          session.isSeller
            ? 'You may sell to anyone inside the scope of your region: the city and state on the customer record decide it, so register the full address.'
            : 'Holders and their dependents are patients.'
        }
      >
        <input
          type="search"
          placeholder="Search name, CPF, phone…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <span className="count">{visible.length} records</span>
        <Link className="button" to="/customers/new">New customer</Link>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>CPF</th>
              <th>Birth date</th>
              <th>Phone</th>
              <th>City</th>
              <th>Plan</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.document}</td>
                <td>{formatDate(row.birth_date)}</td>
                <td>{row.phone}</td>
                <td>{[row.city, row.state].filter(Boolean).join(' / ')}</td>
                <td>
                  {plans[row.id] ? (
                    <>
                      <Link to={`/subscriptions/${plans[row.id].id}`} className={`badge ${plans[row.id].status}`}>
                        {statusLabel(SUBSCRIPTION_STATUS, plans[row.id].status)}
                      </Link>
                      {/* Employees of a partner company: the corporate plan's company. */}
                      {plans[row.id].partner_name && <><br /><small className="muted">Corporate · {plans[row.id].partner_name}</small></>}
                    </>
                  ) : (
                    <span className="badge none">No plan</span>
                  )}
                </td>
                <td className="actions">
                  <Link className="button ghost" to={`/customers/${row.id}`}>Open</Link>
                  {session.isSeller && !plans[row.id] && <Link className="button ghost" to={`/subscriptions/new?customer=${row.id}`}>Sell a plan</Link>}
                  {session.canDeleteCustomers && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {visible.length === 0 && <tr><td colSpan={7} className="muted">No customers found.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
