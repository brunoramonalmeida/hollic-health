import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../api'
import { formatDate, isoDate, today } from '../dates'
import { COMPANY_INVOICE_BADGE, COMPANY_INVOICE_STATUS, INSTALLMENT_STATUS, monthLabel, statusLabel } from '../labels'

/** Can the staff register a payment on this invoice now? */
function payable(invoice) {
  const started = `${invoice.period}-01` <= isoDate(today())
  const fees = invoice.company_fees_open_cents > 0
  const shortfall = invoice.shortfall_cents > 0 && !invoice.shortfall_paid_on && invoice.month_ended
  return started && (fees || shortfall)
}

/** A part of a fee: its amount and when it was paid. */
export function Part({ cents, paidOn }) {
  if (!cents) return <span className="muted">—</span>
  return (
    <>
      {money(cents)}
      <br />
      <small className={paidOn ? 'muted' : 'warn-text'}>{paidOn ? `paid ${formatDate(paidOn)}` : 'open'}</small>
    </>
  )
}

function Shortfall({ invoice }) {
  if (!invoice.shortfall_cents) return <span className="muted">—</span>
  return (
    <>
      <strong>{money(invoice.shortfall_cents)}</strong>
      <br />
      <small className="muted">
        {invoice.shortfall_paid_on
          ? `paid ${formatDate(invoice.shortfall_paid_on)}`
          : invoice.shortfall_final
            ? `due ${formatDate(invoice.shortfall_due_on)}`
            : `${invoice.period}-01` > isoDate(today())
              ? 'projected'
              : 'projected: the month is running'}
      </small>
    </>
  )
}

/**
 * The company's monthly invoices of an agreement: its part of its employees'
 * fees due in each month (all of each fee when the company pays them, its
 * share in a co-payment, none when the employees pay) and, for a month
 * billed to fewer employees than the minimum, the shortfall — the discounts
 * of the month, which the company pays back once the month ended.
 *
 * `basePath`: `/agreements/:id` for the staff, `/portal/agreements/:id` for
 * the company. `canPay`: the staff registers the payments (and may undo a
 * shortfall payment). `employeeLink(line)`: where an employee's name leads.
 */
export default function CompanyInvoices({ invoices, payer, minEmployees, basePath, canPay = false, onChanged, employeeLink, onError }) {
  const [open, setOpen] = useState(null) // the invoice whose lines are shown
  const companyPays = payer === 'company'
  const shared = payer === 'shared'
  const next = invoices.find((item) => canPay && payable(item))

  async function run(action) {
    try {
      await action()
    } catch (err) {
      if (onError) onError(err.message)
    }
  }

  function toggle(period) {
    if (open && open.period === period) {
      setOpen(null)
      return
    }
    run(async () => setOpen(await api(`${basePath}/invoices/${period}`)))
  }

  function pay(invoice) {
    const parts = []
    if (invoice.company_fees_open_cents > 0) parts.push(`${shared ? "the company's part of the fees" : 'fees'} ${money(invoice.company_fees_open_cents)}`)
    if (invoice.shortfall_cents > 0 && !invoice.shortfall_paid_on && invoice.month_ended) parts.push(`shortfall ${money(invoice.shortfall_cents)}`)
    const paidOn = window.prompt(`Payment date of ${monthLabel(invoice.period)} (${parts.join(' + ')})`, isoDate(today()))
    if (!paidOn) return
    run(async () => {
      const detail = await api(`${basePath}/invoices/${invoice.period}/pay`, {
        method: 'POST',
        body: { paid_on: paidOn },
        message: `Invoice ${invoice.period} registered as paid`,
      })
      if (onChanged) await onChanged()
      setOpen(detail)
    })
  }

  function undoShortfall(invoice) {
    if (!window.confirm(`Undo the shortfall payment of ${monthLabel(invoice.period)}?`)) return
    run(async () => {
      const detail = await api(`${basePath}/invoices/${invoice.period}/shortfall`, { method: 'DELETE', message: 'Shortfall payment undone' })
      if (onChanged) await onChanged()
      setOpen(detail)
    })
  }

  return (
    <div className="card" data-testid="company-invoices">
      <div className="card-head">
        <h2>{payer === 'employee' ? "Company's invoices (shortfall only)" : "Company's invoices"}</h2>
        <span className="muted">
          {companyPays ? 'The fees due in each month' : shared ? "The company's part of the fees due in each month (the employees pay theirs)" : 'The employees pay their own fees'};
          below {minEmployees} employees the month's discounts are paid back
        </span>
      </div>
      <table>
        <thead>
          <tr><th>Month</th><th>Employees</th><th>Fees</th><th>Shortfall</th><th>Company owes</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => (
            <Fragment key={invoice.period}>
              <tr>
                <td>
                  {monthLabel(invoice.period)}
                  <br />
                  <small className="muted">
                    due {formatDate(invoice.first_due_on)}{invoice.last_due_on !== invoice.first_due_on && ` – ${formatDate(invoice.last_due_on)}`}
                  </small>
                </td>
                <td>
                  <span className={`badge ${invoice.below_minimum ? 'over-limit' : 'covered'}`}>
                    {invoice.employees} / {invoice.min_employees}
                  </span>
                </td>
                <td>
                  {money(invoice.amount_cents)}
                  <br />
                  <small className="muted">
                    {shared && `company ${money(invoice.company_share_cents)} · employees ${money(invoice.employee_share_cents)}`}
                    {companyPays && (invoice.company_fees_open_cents > 0 ? `${money(invoice.company_fees_open_cents)} open` : `paid ${formatDate(invoice.company_paid_on)}`)}
                    {payer === 'employee' && 'paid by the employees'}
                  </small>
                  {shared && invoice.employee_open_cents > 0 && (
                    <><br /><small className="muted">employees' parts open: {money(invoice.employee_open_cents)}</small></>
                  )}
                </td>
                <td><Shortfall invoice={invoice} /></td>
                <td>
                  <strong>{money(invoice.company_cents)}</strong>
                  {invoice.company_open_cents > 0 && invoice.company_open_cents !== invoice.company_cents && (
                    <><br /><small className="muted">{money(invoice.company_open_cents)} open</small></>
                  )}
                </td>
                <td>
                  <span className={`badge ${COMPANY_INVOICE_BADGE[invoice.status]}`}>{statusLabel(COMPANY_INVOICE_STATUS, invoice.status)}</span>
                </td>
                <td className="actions">
                  <button type="button" className="ghost" onClick={() => toggle(invoice.period)}>
                    {open && open.period === invoice.period ? 'Hide' : 'Details'}
                  </button>
                  {canPay && payable(invoice) && (
                    <button type="button" className={next && next.period === invoice.period ? '' : 'ghost'} onClick={() => pay(invoice)}>
                      Register payment
                    </button>
                  )}
                  {canPay && invoice.shortfall_paid_on && (
                    <button type="button" className="ghost" onClick={() => undoShortfall(invoice)}>Undo shortfall</button>
                  )}
                </td>
              </tr>
              {open && open.period === invoice.period && (
                <tr>
                  <td colSpan={7}>
                    {open.below_minimum && (
                      <p className="notice warn">
                        {open.employees} employee{open.employees === 1 ? '' : 's'} billed for a minimum of {open.min_employees}: the company pays back the
                        discounts of the month ({money(open.discount_cents)}){open.shortfall_final ? '' : ' — projected until the month ends'}.
                      </p>
                    )}
                    <table>
                      <thead>
                        <tr><th>Employee</th><th>Plan</th><th>#</th><th>Due</th><th>Fee</th><th>Company part</th><th>Employee part</th><th>Status</th></tr>
                      </thead>
                      <tbody>
                        {open.lines.map((line) => (
                          <tr key={line.installment_id}>
                            <td>{employeeLink ? <Link to={employeeLink(line)}>{line.customer_name}</Link> : line.customer_name}</td>
                            <td>{line.modality_name}</td>
                            <td>{line.sequence}</td>
                            <td>{formatDate(line.due_on)}</td>
                            <td>
                              {money(line.amount_cents)}
                              {line.discount_cents > 0 && <small className="muted"> · {money(line.discount_cents)} off</small>}
                            </td>
                            <td><Part cents={line.company_share_cents} paidOn={line.company_paid_on} /></td>
                            <td><Part cents={line.employee_share_cents} paidOn={line.employee_paid_on} /></td>
                            <td><span className={`badge ${line.status}`}>{statusLabel(INSTALLMENT_STATUS, line.status)}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {invoices.length === 0 && <tr><td colSpan={7} className="muted">No fees yet: they appear as employees are enrolled.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
