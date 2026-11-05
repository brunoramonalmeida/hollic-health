import { useEffect, useState } from 'react'
import { api } from '../api'
import PageHeader from '../components/PageHeader'
import { formatDateTime } from '../dates'
import { toast } from '../toast'

const STATUSES = ['', 'pending', 'queued', 'processing', 'done', 'dead']

/** Delivery queue (emails) with retries; administrators only. */
export default function Queue() {
  const [rows, setRows] = useState([])
  const [stats, setStats] = useState(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function load() {
    const [jobs, counts] = await Promise.all([api(`/queue${status ? `?status=${status}` : ''}`), api('/queue/stats')])
    setRows(jobs)
    setStats(counts)
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [status])

  async function run() {
    const result = await api('/queue/run', {
      method: 'POST',
      message: false,
    })
    const { delivered, failed } = result.queue
    const reminders = result.reminders.day + result.reminders.hour
    toast[failed ? 'error' : 'success'](`Worker pass: ${delivered} delivered, ${failed} failed, ${reminders} reminders created`)
    await load()
  }

  async function retry(job) {
    await api(`/queue/${job.id}/retry`, { method: 'POST', message: `Job #${job.id} queued again` })
    await load()
  }

  async function retryDead() {
    await api('/queue/retry-dead', { method: 'POST', message: 'Dead jobs queued again' })
    await load()
  }

  return (
    <section>
      <PageHeader
        crumb="Management / Delivery queue"
        title="Delivery queue"
        hint="Jobs (emails, monthly payout closings) are written with the event, relayed to the broker once confirmed, consumed by the worker and acknowledged; failures are retried with back-off, then parked as dead."
      >
        <button type="button" className="ghost" onClick={run}>Run worker now</button>
        {stats && stats.dead > 0 && <button type="button" onClick={retryDead}>Retry all dead ({stats.dead})</button>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      {stats && (
        <div className="kpis four">
          <div className="kpi"><span>Pending</span><strong>{stats.pending}</strong></div>
          <div className="kpi"><span>Queued in broker</span><strong>{stats.queued}</strong><small>{stats.broker === 'rabbitmq' ? `RabbitMQ · ${stats.broker_depth ?? '?'} waiting` : 'in-process broker'}</small></div>
          <div className="kpi"><span>Processing</span><strong>{stats.processing}</strong></div>
          <div className="kpi"><span>Done</span><strong>{stats.done}</strong></div>
          <div className={`kpi ${stats.dead ? 'danger' : ''}`}><span>Dead</span><strong>{stats.dead}</strong></div>
        </div>
      )}
      <div className="tabs">
        {STATUSES.map((value) => (
          <button key={value || 'all'} type="button" className={status === value ? 'tab active' : 'tab'} onClick={() => setStatus(value)}>
            {value || 'All'}
          </button>
        ))}
      </div>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Event</th>
              <th>To</th>
              <th>Subject</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Next run / finished</th>
              <th>Last error</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((job) => (
              <tr key={job.id}>
                <td>{job.id}</td>
                <td>{job.event}</td>
                <td>{job.to}</td>
                <td>{job.subject}</td>
                <td><span className={`badge job-${job.status}`}>{job.status}</span></td>
                <td>{job.attempts}/{job.max_attempts}</td>
                <td>{formatDateTime(job.finished_at || job.run_at)}</td>
                <td className="error-cell">{job.last_error}</td>
                <td className="actions">
                  {(job.status === 'dead' || job.status === 'pending') && (
                    <button type="button" className="ghost" onClick={() => retry(job)}>Retry now</button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9} className="muted">No jobs.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
