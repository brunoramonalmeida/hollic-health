import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fromCents, money, toCents } from '../api'
import PageHeader from '../components/PageHeader'
import { useSession } from '../session'

function blank(fields) {
  return Object.fromEntries(
    fields.map((field) => [field.name, field.money && !field.optional ? '0.00' : field.number ? '0' : '']),
  )
}

function toForm(row, fields) {
  const next = {}
  fields.forEach((field) => {
    if (field.money) next[field.name] = field.optional && row[field.name] == null ? '' : fromCents(row[field.name])
    else next[field.name] = row[field.name] ?? ''
  })
  return next
}

function toBody(form, fields) {
  const body = {}
  fields.forEach((field) => {
    // An optional money field left blank is sent as null ("not set").
    if (field.money) body[field.name] = field.optional && String(form[field.name]).trim() === '' ? null : toCents(form[field.name])
    else if (field.number) body[field.name] = Number(form[field.name] || 0)
    else body[field.name] = form[field.name]
  })
  return body
}

/** Read-only table of a catalog entity with links to the form page. */
export function SimpleList({ title, path, columns, hint }) {
  const { canEditPricing } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api(path))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [path])

  async function remove(row) {
    if (!window.confirm(`Delete "${row.name}"?`)) return
    setError('')
    try {
      await api(`${path}/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader crumb={`Records / ${title}`} title={title} hint={hint}>
        <span className="count">{rows.length} records</span>
        {canEditPricing && <Link className="button" to={`${path}/new`}>New</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              {columns.map((column) => <th key={column.key}>{column.label}</th>)}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                {columns.map((column) => (
                  <td key={column.key}>
                    {column.money
                      ? row[column.key] == null
                        ? <span className="muted">{column.blank || '—'}</span>
                        : money(row[column.key])
                      : row[column.key]}
                  </td>
                ))}
                <td className="actions">
                  {canEditPricing && <Link className="button ghost" to={`${path}/${row.id}`}>Edit</Link>}
                  {canEditPricing && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={columns.length + 1} className="muted">No records yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Create/edit page of a catalog entity; `:id` in the route selects the record. */
export function SimpleForm({ title, path, fields, singular }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const [form, setForm] = useState(blank(fields))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    if (!id) {
      setForm(blank(fields))
      return
    }
    api(`${path}/${id}`)
      .then((row) => setForm(toForm(row, fields)))
      .catch((err) => setError(err.message))
  }, [id, path])

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api(id ? `${path}/${id}` : path, {
        method: id ? 'PUT' : 'POST',
        body: toBody(form, fields),
      })
      navigate(path)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: path, label: title }}
        title={id ? `Edit ${singular}` : `New ${singular}`}
      />
      <form className="panel form-page" onSubmit={save}>
        <div className="fields">
          {fields.map((field) => (
            <label key={field.name}>
              {field.label}
              <input
                value={form[field.name]}
                inputMode={field.money || field.number ? 'decimal' : undefined}
                placeholder={field.placeholder}
                onChange={(event) => setForm({ ...form, [field.name]: event.target.value })}
              />
              {field.hint && <small className="hint">{field.hint}</small>}
            </label>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>
          <Link className="button ghost" to={path}>Cancel</Link>
        </div>
      </form>
    </section>
  )
}
