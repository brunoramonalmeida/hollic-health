import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'
import { allowanceShort } from '../../labels'

export default function PlanList() {
  const session = useSession()
  const [params, setParams] = useSearchParams()
  const categoryFilter = params.get('category') || ''
  const [rows, setRows] = useState([])
  const [categories, setCategories] = useState([])
  const [error, setError] = useState('')

  async function load() {
    const [plans, groups] = await Promise.all([api('/modalities'), api('/plan-categories')])
    setRows(plans)
    setCategories(groups)
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  function filter(value) {
    const next = new URLSearchParams(params)
    if (value) next.set('category', value)
    else next.delete('category')
    setParams(next, { replace: true })
  }

  const visible = rows.filter(
    (row) => !categoryFilter || (categoryFilter === 'none' ? !row.category_id : String(row.category_id) === categoryFilter),
  )

  async function remove(row) {
    if (!window.confirm(`Delete plan "${row.name}"?`)) return
    setError('')
    try {
      await api(`/modalities/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Records / Plans"
        title="Plans"
        hint="Each plan grants a discount on a number of appointments per period: a default percentage, with overrides per specialty. Over the limit, appointments pay the full price. Enterprise plans are only available through company agreements."
      >
        <select value={categoryFilter} aria-label="Category" onChange={(event) => filter(event.target.value)}>
          <option value="">All categories</option>
          {categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          <option value="none">No category</option>
        </select>
        <span className="count">{visible.length} records</span>
        {session.canEditPricing && <Link className="button" to="/modalities/new">New plan</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Monthly price</th>
              <th>Dependents</th>
              <th>Extra dependent</th>
              <th>Waiting</th>
              <th>Discount</th>
              <th>Appointments</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id}>
                <td>
                  {row.name}
                  {row.category_name && (
                    <>
                      <br />
                      {row.enterprise ? <span className="badge enterprise">{row.category_name}</span> : <small className="muted">{row.category_name}</small>}
                    </>
                  )}
                </td>
                <td>{money(row.plan_value_cents)}</td>
                <td>{row.dependent_limit}</td>
                <td>{money(row.extra_dependent_cents)}</td>
                <td>
                  {row.effective_grace_days} days
                  {row.effective_grace_days !== row.grace_days && <><br /><small className="muted">the category's</small></>}
                </td>
                <td>
                  {row.discount_percent}%
                  {row.discounts.length > 0 && (
                    <small className="muted">
                      {' '}· {row.discounts.map((item) => `${item.specialty_name} ${item.percent}%`).join(', ')}
                    </small>
                  )}
                </td>
                <td>{allowanceShort(row.appointment_limit, row.appointment_limit_period)}</td>
                <td className="actions">
                  <Link className="button ghost" to={`/modalities/${row.id}`}>{session.canEditPricing ? 'Edit' : 'View'}</Link>
                  {session.canEditPricing && (
                    <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
            {visible.length === 0 && <tr><td colSpan={8} className="muted">No plans.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
