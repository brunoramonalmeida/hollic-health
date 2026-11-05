import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

export default function PartnerList() {
  const { canManagePartners } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/partners'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete partner "${row.name}"?`)) return
    setError('')
    try {
      await api(`/partners/${row.id}`, { method: 'DELETE', message: 'Partner deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Corporate / Partner companies"
        title="Partner companies"
        hint="Companies the plans are negotiated with directly, for their employees. Each agreement sets the plans, the special conditions and the minimum of employees they require."
      >
        <span className="count">{rows.length} records</span>
        {canManagePartners && <Link className="button" to="/partners/new">New partner</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th>Contact</th>
              <th>Agreements</th>
              <th>Employees enrolled</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  <br />
                  <small className="muted">{[row.cnpj, row.city && `${row.city}/${row.state}`].filter(Boolean).join(' · ') || '—'}</small>
                </td>
                <td>
                  {row.contact_name || <span className="muted">—</span>}
                  {(row.email || row.phone) && <><br /><small className="muted">{[row.email, row.phone].filter(Boolean).join(' · ')}</small></>}
                </td>
                <td>
                  <Link to={`/agreements?partner=${row.id}`}>{row.agreements_in_force} in force</Link>
                  {row.agreements_count > row.agreements_in_force && (
                    <small className="muted"> · {row.agreements_count - row.agreements_in_force} other</small>
                  )}
                </td>
                <td>{row.employees}</td>
                <td><span className={`badge ${row.active ? 'ativa' : 'vencida'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                <td className="actions">
                  <Link className="button ghost" to={`/partners/${row.id}`}>{canManagePartners ? 'Edit' : 'View'}</Link>
                  {canManagePartners && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="muted">No partner companies yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
