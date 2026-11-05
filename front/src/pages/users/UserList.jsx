import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { ROLE_LABEL, useSession } from '../../session'

export default function UserList() {
  const session = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/users'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete user "${row.name}"?`)) return
    setError('')
    try {
      await api(`/users/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Management / Users"
        title="Users"
        hint={session.canManageStaffUsers ? 'Staff, client and professional logins.' : 'Client logins. Staff and professional accounts are managed by administrators.'}
      >
        <span className="count">{rows.length} records</span>
        <Link className="button" to="/users/new">New user</Link>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Linked to</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.email}</td>
                <td><span className={`badge role-${row.role}`}>{ROLE_LABEL[row.role] || row.role}</span></td>
                <td>{row.customer_name || row.professional_name || row.seller_name || row.partner_name || '—'}</td>
                <td className="actions">
                  <Link className="button ghost" to={`/users/${row.id}`}>Edit</Link>
                  {row.id !== session.user.id && (
                    <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="muted">No users.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
