import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

export default function CategoryList() {
  const { canEditPricing } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/plan-categories'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete category "${row.name}"?`)) return
    setError('')
    try {
      await api(`/plan-categories/${row.id}`, { method: 'DELETE', message: 'Category deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Records / Plan categories"
        title="Plan categories"
        hint="Group the plans. An enterprise category's plans are only available through company agreements; a category may set the waiting period of all its plans."
      >
        <span className="count">{rows.length} records</span>
        {canEditPricing && <Link className="button" to="/plan-categories/new">New category</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr><th>Category</th><th>Available</th><th>Waiting period</th><th>Plans</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  {row.description && <><br /><small className="muted">{row.description}</small></>}
                </td>
                <td>
                  {row.enterprise ? <span className="badge enterprise">Enterprise: company agreements only</span> : 'To everyone'}
                </td>
                <td>{row.grace_days === null ? <span className="muted">each plan's own</span> : row.grace_days === 0 ? 'none' : `${row.grace_days} days`}</td>
                <td><Link to={`/modalities?category=${row.id}`}>{row.plans_count}</Link></td>
                <td className="actions">
                  <Link className="button ghost" to={`/plan-categories/${row.id}`}>{canEditPricing ? 'Edit' : 'View'}</Link>
                  {canEditPricing && row.plans_count === 0 && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="muted">No categories yet: plans have none.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
