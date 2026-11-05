import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import { mapLinks } from '../../components/LocationMap'
import PageHeader from '../../components/PageHeader'
import { capacityLabel } from '../../labels'
import { useSession } from '../../session'

export default function ClinicList() {
  const { canEditPricing } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/clinics'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete "${row.name}"?`)) return
    try {
      await api(`/clinics/${row.id}`, { method: 'DELETE', message: 'Clinic deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader crumb="Records / Clinics" title="Clinics" hint="The address and the map are shown to patients on every appointment. State and IBGE code select the municipal tax rules.">
        <span className="count">{rows.length} records</span>
        {canEditPricing && <Link className="button" to="/clinics/new">New clinic</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th>Phone</th>
              <th>Fee</th>
              <th>Capacity</th>
              <th>Map</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const links = mapLinks({ latitude: row.latitude, longitude: row.longitude, address: row.address })
              return (
                <tr key={row.id}>
                  <td>
                    <strong>{row.name}</strong>
                    <br />
                    <small className="muted">{row.cnpj}</small>
                  </td>
                  <td>
                    {row.address || <span className="muted">No address</span>}
                    {row.latitude == null && row.address && <small className="muted"> · no coordinates</small>}
                  </td>
                  <td>{row.phone}</td>
                  <td>
                    {money(row.fee_cents)}
                    {row.on_call_fee_cents != null && <small className="muted"> · on-call {money(row.on_call_fee_cents)}</small>}
                    {row.remote_fee_cents != null && <small className="muted"> · remote {money(row.remote_fee_cents)}</small>}
                  </td>
                  <td>{capacityLabel(row.daily_limit, row.monthly_limit)}</td>
                  <td>{links ? <a href={links.google} target="_blank" rel="noreferrer">Directions</a> : '—'}</td>
                  <td className="actions">
                    <Link className="button ghost" to={`/clinics/${row.id}`}>{canEditPricing ? 'Edit' : 'View'}</Link>
                    {canEditPricing && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">No clinics.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
