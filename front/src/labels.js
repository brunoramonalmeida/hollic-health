export const APPOINTMENT_STATUS = {
  agendada: 'Scheduled',
  realizada: 'Completed',
  cancelada: 'Cancelled',
}

/** Derived by the API from dates and payments; never edited directly. */
export const SUBSCRIPTION_STATUS = {
  pendente: 'Pending payment',
  ativa: 'Active',
  inadimplente: 'Overdue',
  vencida: 'Expired',
  cancelada: 'Cancelled',
  migrada: 'Upgraded',
}

export const INSTALLMENT_STATUS = {
  open: 'Open',
  paid: 'Paid',
  overdue: 'Overdue',
}

/** Period of the plan's appointment allowance (Modality.appointment_limit_period). */
export const LIMIT_PERIOD = {
  month: 'Month (billing period)',
  subscription: 'Subscription period',
}

/** "4 appointments per month", "Unlimited appointments"... */
export function allowanceLabel(limit, period) {
  if (!limit) return 'Unlimited appointments'
  const unit = period === 'subscription' ? 'per subscription period' : 'per month'
  return `${limit} appointment${limit === 1 ? '' : 's'} ${unit}`
}

/** Short form for tables: "4/month", "10/subscription", "Unlimited". */
export function allowanceShort(limit, period) {
  if (!limit) return 'Unlimited'
  return `${limit}/${period === 'subscription' ? 'subscription' : 'month'}`
}

/** "6/day · 100/month", "8/day", "Unlimited" — service capacity reserved for the network. */
export function capacityLabel(dailyLimit, monthlyLimit) {
  const parts = []
  if (dailyLimit > 0) parts.push(`${dailyLimit}/day`)
  if (monthlyLimit > 0) parts.push(`${monthlyLimit}/month`)
  return parts.length ? parts.join(' · ') : 'Unlimited'
}

export function statusLabel(map, value) {
  return map[value] || value
}

/** Corporate agreement (B2B) status, derived by the API from its dates and activity. */
export const AGREEMENT_STATUS = {
  scheduled: 'Scheduled',
  in_force: 'In force',
  ended: 'Ended',
  suspended: 'Suspended',
}

/** Badge class of an agreement status (reuses the subscription colours). */
export const AGREEMENT_BADGE = {
  scheduled: 'pendente',
  in_force: 'ativa',
  ended: 'vencida',
  suspended: 'cancelada',
}

/** Who pays the employees' fees of a corporate agreement. */
export const PAYER = {
  company: 'Paid by the company',
  shared: 'Co-payment',
  employee: 'Paid by each employee',
}

/** "Paid by the company", "Co-payment: company 60% · employee 40%"… */
export function payerLabel(payer, companyPercent) {
  if (payer === 'shared' && companyPercent !== null && companyPercent !== undefined) {
    return `Co-payment: company ${companyPercent}% · employee ${100 - companyPercent}%`
  }
  return PAYER[payer] || payer
}

/** The employee's own part of a fee (API: EmployeeStatementLine.own_status). */
export const OWN_PART_STATUS = {
  paid: 'Paid',
  open: 'Open',
  overdue: 'Overdue',
  none: 'Nothing to pay',
}

/** "12 of 10 employees", the headcount against the agreement's minimum. */
export function headcountLabel(employees, minimum) {
  return `${employees} of ${minimum} employee${minimum === 1 ? '' : 's'}`
}

/** "Waiting period: none" / "the plan's (30 days)". */
export function graceLabel(graceDays, planGraceDays) {
  if (graceDays === null || graceDays === undefined) return planGraceDays > 0 ? `the plan's (${planGraceDays} days)` : "the plan's (none)"
  return graceDays === 0 ? 'none' : `${graceDays} days`
}

/** What the company owes for a month (API: InvoiceOut.status). */
export const COMPANY_INVOICE_STATUS = {
  paid: 'Paid',
  open: 'Open',
  overdue: 'Overdue',
  nothing_due: 'Nothing due',
}

/** Badge class of a company invoice status. */
export const COMPANY_INVOICE_BADGE = {
  paid: 'paid',
  open: 'open',
  overdue: 'overdue',
  nothing_due: 'none',
}

/** "2026-10" -> "October 2026". */
export function monthLabel(period) {
  if (!period) return ''
  const [year, month] = period.split('-').map(Number)
  return new Date(year, month - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}
