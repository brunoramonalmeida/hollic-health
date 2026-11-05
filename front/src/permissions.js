/**
 * Role permissions, mirrored from the API rules (app/auth.py). The API is
 * the authority; these only decide what the UI shows. Pure module (no React)
 * so it can be unit-tested directly.
 */

export const ROLE_LABEL = {
  admin: 'Administrator',
  supervisor: 'Supervisor',
  attendant: 'Attendant',
  client: 'Client',
  professional: 'Professional',
  seller: 'Seller',
  partner: 'Partner company',
}

export const STAFF_ROLES = ['admin', 'supervisor', 'attendant']

export function permissionsFor(user) {
  const role = user?.role
  const staff = STAFF_ROLES.includes(role)
  const professional = role === 'professional'
  const seller = role === 'seller'
  const partner = role === 'partner'
  return {
    role,
    isClient: role === 'client',
    isStaff: staff,
    isProfessional: professional,
    isSeller: seller,
    // a partner company's login: its self-service portal (employees, agreements, invoices), nothing else
    isPartner: partner,
    customerId: user?.customer_id ?? null,
    professionalId: user?.professional_id ?? null,
    sellerId: user?.seller_id ?? null,
    partnerId: user?.partner_id ?? null,
    // plans, discounts, specialties, clinic fees, professionals' fees and hours
    canEditPricing: role === 'admin' || role === 'supervisor',
    canManageTimeOff: staff,
    // customer records: the staff and the sellers (a seller sells to anyone inside its region)
    canManageCustomers: staff || seller,
    canDeleteCustomers: staff,
    // staff and clients book; professionals only sign; sellers have no part in appointments
    canBookAppointments: staff || role === 'client',
    // subscriptions (plan sales): the staff every one, clients their own, sellers their own sales
    canSeeSubscriptions: staff || role === 'client' || seller,
    canSellPlans: staff || seller,
    // staff and clients change a subscription directly; a seller asks for a
    // change (edit or cancellation) that the customer confirms — the client's
    // own login, or the staff recording the client's answer
    canEditSubscriptions: staff || role === 'client',
    canRequestSaleChanges: seller,
    canDecideSaleChanges: staff || role === 'client',
    canDeleteAppointments: staff,
    canRegisterPayments: staff,
    canManageUsers: role === 'admin' || role === 'supervisor',
    // the company portal: enroll and remove the company's own employees, agree their co-payment split
    canManageOwnEmployees: partner,
    // statements: a client checks its own payments; the staff reads every company's and customer's
    canSeeOwnPayments: role === 'client',
    canSeeCompanyStatements: staff,
    canManageStaffUsers: role === 'admin',
    canSeeBalanceReports: role === 'admin',
    canSeeAttendantReport: role === 'admin' || role === 'supervisor',
    // advances, settlements, installments and receipts of professionals and clinics
    canManagePayouts: role === 'admin',
    // sales regions and sellers: the staff reads them (to record a seller's
    // sale on a subscription); admins and supervisors edit them (commission
    // and special discounts move money, like the price list)
    canSeeSales: staff,
    canManageSales: role === 'admin' || role === 'supervisor',
    canRecordSellerSales: staff,
    canSeeCommissions: role === 'admin',
    // corporate partnerships (B2B): the staff reads partner companies and
    // their agreements, enrolls employees and registers the company's
    // invoices; admins and supervisors negotiate them (discounts move money)
    canSeePartners: staff,
    canManagePartners: role === 'admin' || role === 'supervisor',
    canEnrollEmployees: staff,
    canPayInvoices: staff,
    // the seller's own area: its sales, portfolio, region, commissions and account
    canSeeOwnSales: seller,
    // the professional's own area: sign appointments, follow the payout
    canSignAppointments: professional,
    canSeeOwnPayouts: professional,
  }
}

/** Can this user sign the given appointment? The API decides (`can_sign`); the role check avoids showing the button to others. */
export function canSign(permissions, appointment) {
  return Boolean(permissions.canSignAppointments && appointment && appointment.can_sign)
}
